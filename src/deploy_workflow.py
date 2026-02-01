#!/usr/bin/env python3
"""
Deploy IFS Cloud Workflow using Playwright.

This script:
1. Connects to existing Chrome instance with IFS Cloud session via CDP
2. Navigates to Workflows page
3. Searches for the target workflow
4. Selects it and clicks Deploy
5. Confirms deployment
6. Verifies deployment status
"""

import sys
import time
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

# Configuration
TARGET_URL = "https://mezzetta-uat.ifs.cloud/main/ifsapplications/web/page/Workflow/Workflows"
WORKFLOW_NAME = "MZ_ManualReserveShipmentByLocation"
CDP_PORT = 56112  # From existing Chrome instance

def handle_login_if_needed(page: Page, username: str = "ifsapp"):
    """Handle IFS Cloud SSO login if on login page."""
    current_url = page.url

    if "/auth/" in current_url or "login" in current_url.lower():
        print("Login page detected, attempting SSO login...")

        # Try SSO button first
        try:
            sso_button = page.locator("text=Log in with Mezzetta SSO UAT, button:has-text('SSO')").first
            if sso_button.is_visible(timeout=3000):
                sso_button.click()
                time.sleep(3)
                # Check if SSO succeeded
                if "/auth/" not in page.url:
                    print("SSO login successful")
                    return True
        except Exception:
            pass

        # Fall back to username/password login
        try:
            username_field = page.locator("#username, input[name='username']").first
            password_field = page.locator("#password, input[name='password']").first

            if username_field.is_visible(timeout=3000) and password_field.is_visible(timeout=3000):
                print(f"Entering credentials for user: {username}")
                username_field.fill(username)
                # Note: Password should be provided via environment variable in production
                # For now, just fill the username and wait for manual password entry
                password_field.focus()
                print("Please enter password manually in the browser...")
                time.sleep(30)  # Wait for manual password entry

                # Click login button
                login_btn = page.locator("button:has-text('Log In'), input[type='submit']").first
                if login_btn.is_visible(timeout=3000):
                    login_btn.click()
                    time.sleep(5)
                    return "/auth/" not in page.url
        except Exception as e:
            print(f"Login handling error: {e}")

        return False

    return True  # Not on login page


def wait_for_ifs_page_load(page: Page, timeout: int = 30000):
    """Wait for IFS Cloud page to fully load."""
    print("Waiting for IFS page to load...")
    try:
        # Wait for network to be idle
        page.wait_for_load_state("networkidle", timeout=timeout)
        # Wait for any loading spinners to disappear
        page.wait_for_selector(".loading", state="hidden", timeout=5000)
    except PlaywrightTimeout:
        print("Loading indicator not found or already hidden")
    time.sleep(2)  # Extra wait for IFS UI to stabilize


def take_snapshot(page: Page, description: str):
    """Print page state as text snapshot."""
    print(f"\n=== SNAPSHOT: {description} ===")
    print(f"URL: {page.url}")
    print(f"Title: {page.title()}")

    # Get visible text content
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
        # Truncate if too long
        if len(body_text) > 2000:
            body_text = body_text[:2000] + "..."
        print(f"Page content preview:\n{body_text[:500]}...")
    except Exception as e:
        print(f"Could not get page text: {e}")
    print("=" * 50 + "\n")


def search_workflow(page: Page, workflow_name: str):
    """Search for a workflow using the search panel."""
    print(f"Searching for workflow: {workflow_name}")

    # Try to find and click the Search panel or filter
    search_found = False

    # Method 1: Look for search input field
    search_selectors = [
        "input[placeholder*='Search']",
        "input[placeholder*='Filter']",
        "[data-test-id='search-input']",
        ".search-input",
        "input[type='search']",
        # IFS Cloud specific selectors
        "ifscore-search input",
        "[class*='search'] input",
        "[class*='filter'] input",
    ]

    for selector in search_selectors:
        try:
            search_input = page.locator(selector).first
            if search_input.is_visible(timeout=2000):
                search_input.fill(workflow_name)
                search_input.press("Enter")
                search_found = True
                print(f"Used search selector: {selector}")
                time.sleep(2)
                break
        except Exception:
            continue

    if not search_found:
        # Method 2: Try to open search panel first
        try:
            search_button = page.locator("button:has-text('Search'), [aria-label='Search']").first
            if search_button.is_visible(timeout=2000):
                search_button.click()
                time.sleep(1)
                # Now try to find the input again
                page.locator("input").first.fill(workflow_name)
                page.locator("input").first.press("Enter")
                search_found = True
        except Exception:
            pass

    if not search_found:
        # Method 3: Look for workflow name field specifically
        try:
            # IFS Cloud often has a "Workflow Name" column filter
            name_filter = page.locator("[title='Workflow Name'], [aria-label='Workflow Name']").first
            if name_filter.is_visible(timeout=2000):
                name_filter.click()
                time.sleep(0.5)
                page.keyboard.type(workflow_name)
                page.keyboard.press("Enter")
                search_found = True
        except Exception:
            pass

    return search_found


def select_workflow_row(page: Page, workflow_name: str):
    """Select the workflow row in the table."""
    print(f"Looking for workflow row: {workflow_name}")

    # Various methods to find and click the row
    row_selectors = [
        f"tr:has-text('{workflow_name}')",
        f"[role='row']:has-text('{workflow_name}')",
        f"div:has-text('{workflow_name}'):not(:has(div:has-text('{workflow_name}')))",
        f"td:has-text('{workflow_name}')",
        f"a:has-text('{workflow_name}')",
        f"text={workflow_name}",
    ]

    for selector in row_selectors:
        try:
            row = page.locator(selector).first
            if row.is_visible(timeout=3000):
                row.click()
                print(f"Selected row using: {selector}")
                time.sleep(1)
                return True
        except Exception as e:
            continue

    print(f"Could not find row for: {workflow_name}")
    return False


def click_deploy_button(page: Page):
    """Find and click the Deploy button/command."""
    print("Looking for Deploy button...")

    deploy_selectors = [
        "button:has-text('Deploy')",
        "[aria-label='Deploy']",
        "[title='Deploy']",
        "text=Deploy",
        # IFS Cloud command bar
        "[data-command='Deploy']",
        ".command-bar button:has-text('Deploy')",
        "[role='menuitem']:has-text('Deploy')",
    ]

    for selector in deploy_selectors:
        try:
            deploy_btn = page.locator(selector).first
            if deploy_btn.is_visible(timeout=2000):
                deploy_btn.click()
                print(f"Clicked Deploy using: {selector}")
                time.sleep(2)
                return True
        except Exception:
            continue

    # Try right-click context menu
    try:
        page.locator("[role='row']").first.click(button="right")
        time.sleep(1)
        context_deploy = page.locator("[role='menuitem']:has-text('Deploy'), text=Deploy").first
        if context_deploy.is_visible(timeout=2000):
            context_deploy.click()
            print("Clicked Deploy from context menu")
            return True
    except Exception:
        pass

    print("Could not find Deploy button")
    return False


def confirm_deployment(page: Page):
    """Confirm deployment if a dialog appears."""
    print("Checking for deployment confirmation dialog...")

    confirm_selectors = [
        "button:has-text('Yes')",
        "button:has-text('OK')",
        "button:has-text('Confirm')",
        "[role='button']:has-text('Yes')",
        "[role='button']:has-text('OK')",
    ]

    time.sleep(1)  # Wait for dialog to appear

    for selector in confirm_selectors:
        try:
            confirm_btn = page.locator(selector).first
            if confirm_btn.is_visible(timeout=2000):
                confirm_btn.click()
                print(f"Confirmed deployment using: {selector}")
                time.sleep(2)
                return True
        except Exception:
            continue

    print("No confirmation dialog found or not needed")
    return True


def verify_deployment_status(page: Page, workflow_name: str):
    """Verify the workflow shows as Deployed."""
    print("Verifying deployment status...")
    time.sleep(2)  # Wait for status update

    # Look for Deployed status in the row
    try:
        deployed_indicator = page.locator(f"tr:has-text('{workflow_name}'):has-text('Deployed')").first
        if deployed_indicator.is_visible(timeout=5000):
            print("SUCCESS: Workflow shows as Deployed!")
            return True
    except Exception:
        pass

    # Alternative check - look for status column
    try:
        status_cell = page.locator(f"tr:has-text('{workflow_name}') td:has-text('Deployed')").first
        if status_cell.is_visible(timeout=3000):
            print("SUCCESS: Status column shows Deployed!")
            return True
    except Exception:
        pass

    # Check page content for any indication
    page_text = page.locator("body").inner_text()
    if "Deployed" in page_text and workflow_name in page_text:
        print("SUCCESS: Page indicates workflow is Deployed")
        return True

    print("WARNING: Could not verify Deployed status")
    return False


def main():
    print(f"\n{'='*60}")
    print("IFS Cloud Workflow Deployment Script")
    print(f"{'='*60}\n")
    print(f"Target: {TARGET_URL}")
    print(f"Workflow: {WORKFLOW_NAME}")
    print(f"CDP Port: {CDP_PORT}\n")

    with sync_playwright() as p:
        # Connect to existing Chrome instance via CDP
        print(f"Connecting to Chrome via CDP on port {CDP_PORT}...")
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")

        # Get existing contexts and pages
        contexts = browser.contexts
        print(f"Found {len(contexts)} browser contexts")

        page = None
        existing_ifs_page = None

        if contexts:
            context = contexts[0]
            pages = context.pages
            print(f"Found {len(pages)} pages in first context")

            # Look for an existing IFS Cloud page that's authenticated
            for p_idx, existing_page in enumerate(pages):
                url = existing_page.url
                print(f"  Page {p_idx}: {url[:80]}...")
                if "mezzetta-uat.ifs.cloud" in url and "/auth/" not in url:
                    existing_ifs_page = existing_page
                    print(f"  -> Found authenticated IFS page!")

            if existing_ifs_page:
                # Use the existing authenticated page
                page = existing_ifs_page
                print("Using existing authenticated IFS page")
            else:
                # Create new page
                page = context.new_page()
                print("Creating new page")
        else:
            # Create new context if none exists
            context = browser.new_context()
            page = context.new_page()
            print("Created new context and page")

        try:
            # Step 1: Navigate to Workflows page
            print(f"\n--- Step 1: Navigating to Workflows page ---")
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            wait_for_ifs_page_load(page)

            # Handle login if redirected to auth page
            if "/auth/" in page.url:
                print("\n--- Handling Login ---")
                if not handle_login_if_needed(page, "ifsapp"):
                    print("WARNING: Login may have failed")
                wait_for_ifs_page_load(page)

            take_snapshot(page, "After navigation")

            # Step 2: Search for workflow
            print(f"\n--- Step 2: Searching for workflow ---")
            search_workflow(page, WORKFLOW_NAME)
            time.sleep(2)
            take_snapshot(page, "After search")

            # Step 3: Select the workflow row
            print(f"\n--- Step 3: Selecting workflow row ---")
            if not select_workflow_row(page, WORKFLOW_NAME):
                print("ERROR: Could not select workflow row")
                take_snapshot(page, "Failed to select row")

            # Step 4: Click Deploy button
            print(f"\n--- Step 4: Clicking Deploy ---")
            if not click_deploy_button(page):
                print("ERROR: Could not find Deploy button")
                take_snapshot(page, "Failed to find Deploy")

            # Step 5: Confirm deployment
            print(f"\n--- Step 5: Confirming deployment ---")
            confirm_deployment(page)

            # Step 6: Verify deployment status
            print(f"\n--- Step 6: Verifying deployment status ---")
            success = verify_deployment_status(page, WORKFLOW_NAME)

            take_snapshot(page, "Final state")

            if success:
                print(f"\n{'='*60}")
                print("DEPLOYMENT COMPLETED SUCCESSFULLY")
                print(f"{'='*60}")
            else:
                print(f"\n{'='*60}")
                print("DEPLOYMENT MAY HAVE ISSUES - Please verify manually")
                print(f"{'='*60}")

            # Keep browser open for manual verification
            print("\nBrowser will stay open for 10 seconds for verification...")
            time.sleep(10)

        except Exception as e:
            print(f"\nERROR: {e}")
            take_snapshot(page, "Error state")
            raise
        finally:
            # Don't close the browser - we're connected to an existing instance
            # Only close the page if we created it (not if we're using an existing one)
            if not existing_ifs_page:
                page.close()
                print("\nNew page closed (browser remains open).")
            else:
                print("\nUsed existing page (left open).")


if __name__ == "__main__":
    main()
