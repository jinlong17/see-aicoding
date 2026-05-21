# Installing see-aicoding

## TL;DR (on any new machine)

```bash
pip install --user git+https://github.com/jinlong17/see-aicoding.git
see-aicoding
```

If `see-aicoding` is "command not found" after install, add your pip user-base bin to PATH:

```bash
# macOS:
echo 'export PATH="$HOME/Library/Python/3.11/bin:$PATH"' >> ~/.zshrc
# or, more portable:
echo 'export PATH="$(python3 -m site --user-base)/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## Prerequisites

- Python 3.9+ (3.11+ recommended)
- macOS (tested on 14, 15) or Linux
- `pip` and access to the public internet (for first install)

## First-time GitHub setup (one-time, by the maintainer)

This repo isn't on GitHub yet. Once it is, every other machine just needs the one-line install above.

### Path A — using GitHub web UI (no extra tools needed)

1. Go to https://github.com/new
2. Name it `see-aicoding`, public, no README (we already have one)
3. Run on your local machine:

    ```bash
    cd ~/Desktop/AI_Desktop/see-aicoding
    git remote add origin https://github.com/jinlong17/see-aicoding.git
    git branch -M main
    git push -u origin main
    ```

### Path B — using `gh` CLI

```bash
brew install gh
gh auth login
cd ~/Desktop/AI_Desktop/see-aicoding
gh repo create see-aicoding --public --source=. --remote=origin --push
```

## Updating on an installed machine

```bash
pip install --user --upgrade --force-reinstall git+https://github.com/jinlong17/see-aicoding.git
```

Or if you cloned the repo:

```bash
cd see-aicoding
git pull
pip install -e .
```

## Uninstalling

```bash
pip uninstall see-aicoding
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `see-aicoding: command not found` | Add `$(python3 -m site --user-base)/bin` to PATH |
| `psutil` build fails on Linux | `sudo apt install python3-dev gcc` then retry |
| Permission errors reading other-user processes | Expected — see-aicoding only monitors the current user's processes |
| Terminal characters look broken | Use a Unicode-aware terminal (Wezterm, iTerm2, Alacritty, modern Terminal.app) |
| Empty / no processes detected | Confirm psutil works: `python3 -c "import psutil; print(len(list(psutil.process_iter())))"` |
