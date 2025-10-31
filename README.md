# APISwitch

APISwitch is a profile switcher for Google/Gemini API keys and gcloud projects. It works on Windows, macOS, and Linux.

## Features

*   **Profile Management**: Easily switch between different profiles for your API keys and gcloud projects.
*   **Environment Variable Persistence**:
    *   **Windows**: Persists environment variables in the Registry (HKCU/HKLM) and broadcasts changes using `WM_SETTINGCHANGE`.
    *   **POSIX (macOS/Linux)**: Persists environment variables in `~/.config/apiswitch/env.sh` and automatically sources it in your shell's rc file.
*   **Isolated gcloud Configuration**: All `gcloud` calls use `CLOUDSDK_CONFIG=<selected dir>` to prevent profile mix-ups, especially during elevation.
*   **Deep Purge**: Supports a "deep" purge mode that clears ADC, legacy credentials, `credentials.db`, and `access_tokens.db`.
*   **Project ID/Number Resolution**: Automatically resolves project ID and number.
*   **Sanitized gcloud Configuration Names**: Automatically sanitizes gcloud configuration names (lowercase, a-z, 0-9, -).

## How to Use

1.  Run the `apiswitch.py` script.
2.  The application window will appear, allowing you to manage your profiles.
3.  You can create, delete, and switch between profiles.
4.  The "Analyze Current Setup" button helps you detect your current configuration.
5.  The "Apply" button applies the selected profile's settings.

## Configuration



*   **Profiles**: `~/.apiswitch/profiles.json`

*   **Settings**: `~/.apiswitch/settings.json`



## Changelog



### 2025-10-31



*   Added a check to `apiswitch.ps1` to ensure it's run in a PowerShell terminal.
