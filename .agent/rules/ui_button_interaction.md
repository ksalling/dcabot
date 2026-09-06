# UI Button Interaction & Mutual Disabling Rules

All buttons that trigger actions, API/HTMX requests, or form submissions must follow these rules:

1. **Immediate Updating Feedback & Lockout**:
   - Whenever any button is pressed, it must immediately indicate that the system is updating (showing an animated spinner and text such as "Updating...", "Processing...", etc.).
   - The button must immediately be unclickable (`disabled`, `pointer-events-none`, `cursor-not-allowed`).

2. **Choice of Multiple Buttons (Mutual Disabling)**:
   - When presented with two or more choice buttons (e.g. "Keep Paused & Update" vs "Enable & Update Job", or "Confirm" vs "Cancel"):
     - The pressed button shows the updating state and is disabled.
     - All alternative option buttons are simultaneously disabled and rendered unclickable (`pointer-events-none`, `opacity-40`) to prevent duplicate or conflicting commands.
