# PowerShell script to push to GitHub
# Run this after installing Git and creating a GitHub repository

Write-Host "=== Screener Dashboard - GitHub Push Script ===" -ForegroundColor Cyan
Write-Host ""

# Check if git is installed
try {
    $gitVersion = git --version
    Write-Host "Git found: $gitVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Git is not installed or not in PATH" -ForegroundColor Red
    Write-Host "Please install Git from: https://git-scm.com/download/win" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Step 1: Initializing git repository..." -ForegroundColor Yellow
git init

Write-Host ""
Write-Host "Step 2: Adding all files..." -ForegroundColor Yellow
git add .

Write-Host ""
Write-Host "Step 3: Creating initial commit..." -ForegroundColor Yellow
git commit -m "Initial commit: Screener Dashboard App"

Write-Host ""
Write-Host "Step 4: Setting up remote repository..." -ForegroundColor Yellow
Write-Host ""
$githubUrl = Read-Host "Enter your GitHub repository URL (e.g., https://github.com/username/repo-name.git)"

if ($githubUrl) {
    git remote add origin $githubUrl
    Write-Host "Remote added: $githubUrl" -ForegroundColor Green
} else {
    Write-Host "No URL provided. You can add it later with:" -ForegroundColor Yellow
    Write-Host "  git remote add origin YOUR_GITHUB_URL" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Step 5: Renaming branch to 'main'..." -ForegroundColor Yellow
git branch -M main

Write-Host ""
Write-Host "=== Ready to push! ===" -ForegroundColor Green
Write-Host ""
Write-Host "To push to GitHub, run:" -ForegroundColor Cyan
Write-Host "  git push -u origin main" -ForegroundColor White
Write-Host ""
Write-Host "Note: You may be prompted for GitHub credentials." -ForegroundColor Yellow
Write-Host "      Use a Personal Access Token if password authentication fails." -ForegroundColor Yellow

