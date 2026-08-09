# GitHub publishing checklist

## Create the repository

1. Create a new empty GitHub repository, for example `control-deck`.
2. Do not ask GitHub to generate a README, `.gitignore`, or license; they are already included here.
3. Replace `YOUR-USERNAME` in the clone command inside `README.md` with the actual GitHub account or organization name.
4. If desired, replace `Control Deck contributors` in `LICENSE` with the copyright holder's name.
5. Upload the complete contents of this directory while preserving the directory structure.

## Recommended Git commands

Run these commands from this directory:

```bash
git init
git add .
git commit -m "Initial Control Deck release"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/control-deck.git
git push -u origin main
```

## Add or replace screenshots

Store screenshots in `docs/screenshots/`. Use lowercase kebab-case PNG filenames without spaces:

- `control-deck-overview.png`
- `control-deck-system-log.png`
- `control-deck-gesture-matrix.png`
- `control-deck-telemetry.png`
- `control-deck-macros-matrix.png`
- `control-deck-macro-editor.png`

The main README already displays `control-deck-system-log.png`. Replacing that file updates the displayed image automatically after the change is pushed.

To show additional screenshots, add lines like these to the Screenshots section of `README.md`:

```markdown
![Control Deck Gesture Matrix](docs/screenshots/control-deck-gesture-matrix.png)

![Control Deck Telemetry](docs/screenshots/control-deck-telemetry.png)

![Control Deck Macros Matrix](docs/screenshots/control-deck-macros-matrix.png)
```

Before publishing screenshots, hide personal email addresses, Bluetooth addresses, device identifiers, and unrelated desktop content.

## Create the first release

After the initial push:

1. Open **Releases** on GitHub.
2. Select **Draft a new release**.
3. Create tag `v1.0.0` from `main`.
4. Use `Control Deck 1.0.0` as the release title.
5. Copy the `1.0.0` section from `CHANGELOG.md` into the release notes.
6. Attach a ZIP archive only if you want to offer a direct download in addition to GitHub's automatic source archives.
