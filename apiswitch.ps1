# APISwitch PowerShell Wrapper
# Run this script to launch the APISwitch GUI.
# When the GUI is closed, it will apply the environment variables to your current PowerShell session.

# Check if running in PowerShell
if ($PSVersionTable.PSVersion -eq $null) {
    Write-Host "This script must be run in a PowerShell terminal." -ForegroundColor Red
    exit 1
}

# Define the path for the temporary script that the Python app will generate
$tempFile = Join-Path $env:TEMP "apiswitch_apply.ps1"

# Clean up any leftover temp file from a previous run
if (Test-Path $tempFile) {
    Remove-Item $tempFile -ErrorAction SilentlyContinue
}

# Launch the Python GUI.
# The -u flag is for unbuffered output, which can be helpful for debugging.
python -u apiswitch.py

# After the Python script (GUI) closes, check if it created the temp script
if (Test-Path $tempFile) {
    try {
        # Source the temporary script to apply the environment variables
        . $tempFile
        Write-Host "APISwitch environment variables applied to your session." -ForegroundColor Green
    } catch {
        Write-Host "Error applying APISwitch environment variables." -ForegroundColor Red
        Write-Error $_
    } finally {
        # Clean up the temporary script
        Remove-Item $tempFile -ErrorAction SilentlyContinue
    }
}
