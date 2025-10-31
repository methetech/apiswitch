# APISwitch

A slick, cross-platform GUI for managing and rapidly switching between API key profiles. Designed for developers working with Google Cloud, Gemini, and other services who are tired of juggling environment variables.

## Core Features

*   **Seamless Profile Switching**: Create, edit, and switch between different `gcloud` accounts, projects, and API keys in seconds.
*   **Cross-Platform Persistence**:
    *   **Windows**: Securely saves environment variables to the Registry (`HKCU`/`HKLM`) and uses a PowerShell wrapper to inject settings into your **current terminal session**.
    *   **POSIX (Linux/macOS)**: Automatically configures your `.bashrc`, `.zshrc`, or `.profile` to source a dedicated environment file.
*   **Isolated `gcloud` Context**: All `gcloud` commands are run with a sandboxed `CLOUDSDK_CONFIG` to prevent conflicts between user and administrator profiles.
*   **Intelligent `gcloud` Integration**:
    *   Auto-detects `gcloud` installation.
    *   Resolves project IDs and numbers.
    *   Manages `gcloud` configurations for each profile.
*   **Deep Clean**: A "Purge" function to surgically remove `gcloud` authentication caches (`ADC`, `legacy_credentials`, databases) when you need a fresh start.

---

## Getting Started

The correct way to launch APISwitch depends on your operating system. Following these instructions is critical for the application to correctly modify your environment.

### **Windows (The Right Way)**

On Windows, you **must** use the `apiswitch.ps1` wrapper script. This is essential for applying environment variables back to the terminal session you launched it from.

1.  Open a **PowerShell** terminal.
2.  Navigate to the project directory:
    ```powershell
    cd C:\path\to\apiswitch
    ```
3.  Execute the PowerShell script:
    ```powershell
    .\apiswitch.ps1
    ```

> **Warning**: Do **NOT** run `python apiswitch.py` directly in PowerShell or `cmd`. Bypassing the wrapper script will prevent it from updating your current session's environment variables upon closing.

### **macOS & Linux**

On POSIX-based systems, you can run the Python script directly.

1.  Open your preferred terminal.
2.  Navigate to the project directory.
3.  Run the script:
    ```bash
    python3 apiswitch.py
    ```
On the first run, the script will configure your shell's startup file (e.g., `~/.zshrc`, `~/.bashrc`) to source the APISwitch environment. You may need to open a new terminal window or manually source your config (`source ~/.zshrc`) for the changes to take effect.

---

## Administrator Privileges (Windows)

APISwitch is designed to run with **standard user privileges** for maximum security.

It will only request Administrator elevation (via a UAC prompt) if you explicitly check the **"Machine-wide env vars"** option when applying a profile. This ensures that elevated rights are only used when absolutely necessary, preventing accidental changes to the system.
