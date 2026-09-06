# Moondrip Project Rules & Guidelines

## UI Button Interaction & Multi-Click Prevention Rules

All user interface buttons across the application that trigger asynchronous operations, form submissions, modal actions, or server requests must strictly adhere to the following interaction rules:

### 1. Single Button Actions (Loading Feedback & Multi-Click Lockout)
- **Immediate State Change**: When a user clicks any action or submit button, the button must immediately provide visual feedback showing that the system is updating (e.g., animated spinner with "Updating...", "Processing...", "Saving...", or "Deleting...").
- **Unclickable & Disabled**: The button must immediately become unclickable (marked `disabled`, `pointer-events-none`, `cursor-not-allowed`, and styled with `disabled:opacity-50`) to prevent duplicate submissions or multi-clicks while the operation is processing.

### 2. Choice of Multiple Buttons (Mutual Disabling)
- **Selected Button**: When a user is presented with a choice between two or more action buttons (e.g., "Keep Paused & Update" vs. "Enable & Update Job", or "Confirm" vs. "Cancel"):
  - The clicked button must display the animated loading spinner and active progress status (e.g., "Updating...").
  - The clicked button must be disabled and unclickable.
- **Alternative / Companion Options**:
  - All alternative choice buttons (including Cancel, Dismiss, Close, and secondary choices) must **immediately be disabled and rendered unclickable** (`disabled`, `pointer-events-none`, `cursor-not-allowed`, and dimmed with `opacity-40` / `disabled:opacity-50`).
  - No competing commands or secondary clicks may be issued while the selected choice is in flight.

### 3. Standard Implementation Patterns
- **HTMX Requests**: Use `hx-disabled-elt` targeting both the button and companion buttons (e.g., `hx-disabled-elt="#modal-id button, this"`), accompanied by `.htmx-spinner-show`, `.htmx-text-default`, and `.htmx-text-loading` utility classes.
- **Form Submissions & Modal Actions**: Use unified submission handler functions (or form submit locks) that disable all buttons in the modal and container before invoking `.submit()`.
