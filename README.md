# Installation instructions

To run this project, you will require a working Python 3 installation (Python 3.8 or higher recommended).

## Setup Instructions

### 1. Set Up Virtual Environment (Recommended)

It's recommended to use a virtual environment to isolate project dependencies:

**On Unix/macOS/Linux:**

```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate
```

**On Windows (PowerShell):**

```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1
```

**On Windows (Command Prompt):**

```cmd
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.venv\Scripts\activate.bat
```

### 2. Install Dependencies

There are no dependencies required beyond base Python libraries to run the main script.

Depending on whether you decide to package the script into an executable, you may need to install `PyInstaller` (see 3B).

### 3. Running the Script

There are two ways to run the script depending on your platform:

#### Option A: Unix/macOS/Linux - Direct Execution

Make the script executable and run it directly:

```bash
# Make the script executable (only needed once)
chmod +x render-schedule.py

# Run the script
./render-schedule \
    --schedule=schedule.json \
    --overrides=overrides.json \
    --from='2025-11-07T17:00:00Z' \
    --until='2025-11-21T17:00:00Z'
```

The script includes a shebang line (`#!/usr/bin/env python3`) that allows it to be run directly once marked as executable.

#### Option B: Windows - Build Standalone Executable with PyInstaller

On Windows, you can create a standalone executable that doesn't require Python to be installed:

```powershell
# Install PyInstaller (if not already installed)
pip install pyinstaller

# Build single-file executable
pyinstaller --onefile render-schedule.py
# The executable will be created in the dist/ folder

# Move the executable into the root-level directory
mv .\dist\render-schedule.exe render-schedule.exe

# Run the executable
.\render-schedule `
    --schedule=schedule.json `
    --overrides=overrides.json `
    --from='2025-11-07T17:00:00Z' `
    --until='2025-11-21T17:00:00Z'
```

**Note:** The `--onefile` (or `-F`) flag packages everything into a single executable file.

#### Option C: Run with Python Directly (All Platforms)

You can always run the script directly with Python:

```bash
python render-schedule.py \
    --schedule=schedule.json \
    --overrides=overrides.json \
    --from='2025-11-07T17:00:00Z' \
    --until='2025-11-21T17:00:00Z'
```

However, this doesn't quite fit the format given in the problem description.
