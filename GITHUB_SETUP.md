# How to Push to GitHub

Follow these steps to push your project to GitHub:

## Step 1: Install Git (if not installed)

1. Download Git from: https://git-scm.com/download/win
2. Install it with default settings
3. Restart your terminal/PowerShell after installation

## Step 2: Create a GitHub Repository

1. Go to https://github.com and sign in (or create an account)
2. Click the "+" icon in the top right → "New repository"
3. Name it (e.g., "screener-dash-app")
4. **DO NOT** initialize with README, .gitignore, or license (we already have these)
5. Click "Create repository"

## Step 3: Initialize Git and Push (in PowerShell)

Open PowerShell in the `screener-dash-app` folder and run:

```powershell
# Navigate to your project folder
cd C:\Users\Admin\Desktop\stockapp\screener-dash-app

# Initialize git repository
git init

# Add all files
git add .

# Create first commit
git commit -m "Initial commit: Screener Dashboard App"

# Add your GitHub repository as remote (replace YOUR_USERNAME and REPO_NAME)
git remote add origin https://github.com/YOUR_USERNAME/REPO_NAME.git

# Rename branch to main (if needed)
git branch -M main

# Push to GitHub
git push -u origin main
```

## Alternative: Using GitHub Desktop (Easier)

If you prefer a GUI:

1. Download GitHub Desktop: https://desktop.github.com/
2. Install and sign in with your GitHub account
3. Click "File" → "Add Local Repository"
4. Browse to: `C:\Users\Admin\Desktop\stockapp\screener-dash-app`
5. Click "Publish repository" button
6. Choose your GitHub account and repository name
7. Click "Publish Repository"

## Important Notes

- Replace `YOUR_USERNAME` and `REPO_NAME` with your actual GitHub username and repository name
- If you get authentication errors, you may need to use a Personal Access Token instead of password
- The `.gitignore` file will automatically exclude unnecessary files (venv, __pycache__, etc.)

## After Pushing

Your code will be live on GitHub! You can:
- Share the repository URL with others
- Clone it on other machines
- Collaborate with others
- Set up GitHub Actions for CI/CD (optional)

