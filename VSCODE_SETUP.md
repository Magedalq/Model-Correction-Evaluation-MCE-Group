# Running this project in VS Code

## One-time setup
0. **Rename the `_vscode` folder to `.vscode`** (with the dot). Easiest way: open the folder in VS Code, right-click `_vscode` in the sidebar > Rename. Until you do this, F5 and Ctrl+Shift+B won't show the study steps.
1. Install **Python 3.11+** from python.org. On Windows, check "Add python.exe to PATH" during install.
2. In VS Code: **File > Open Folder** and pick this `Repromp` folder (the one holding `pipeline/`, not `__MACOSX`).
3. Install the extensions VS Code suggests (Python + Python Debugger; LaTeX Workshop is optional for the report).
4. Copy `.env.example` to `.env` and paste your ARC key after `ARC_API_KEY=`. No packages need installing; the code only uses Python's standard library.

## Running
- **Ctrl+Shift+B** runs the tests, like a build.
- **F5** opens a dropdown of every study step (preflight, calibrate, select, smoke, audit, analyze, full study). Pick one, answer the prompts, and the output shows in the terminal. You can set breakpoints like in C++.
- Pick `models_arc_only.json` when asked for models until the Qwen server is set up on ARC. It's the same config minus Qwen.
- Every live run needs a **new** output folder (`runs/calibration_1`, `runs/calibration_2`, ...). The script refuses to overwrite an old run on purpose.

## Order
0. Tests → 1. Preflight → 2. Calibrate → 3. Select → 4. Smoke → 5. Audit → 6. Analyze → 7. Full study
