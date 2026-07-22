# Migrating to the Windows 11 Lenovo

Step-by-step for moving the tracker from the old Lenovo (Linux/cron) to the new
Lenovo (Windows 11/Task Scheduler). See `DEPLOYMENT.md` for where it runs today
and `README.md` for a from-scratch setup (OAuth, Sheet, Telegram).

## 1. Install prerequisites
- **Python**: download from [python.org](https://www.python.org/downloads/) (3.11+).
  During install, check **"Add python.exe to PATH"**.
- **Git**: download from [git-scm.com](https://git-scm.com/download/win) (or skip
  and download the repo as a ZIP from GitHub instead).

Verify in PowerShell:
```powershell
python --version
git --version
```

## 2. Get the code
```powershell
mkdir C:\BudgetTracker
cd C:\BudgetTracker
git clone <your-github-url> .
cd app
```

## 3. Set up the virtual environment
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 4. Copy the secrets from the old Lenovo
Git-ignored, so cloning won't bring them — copy these 3 files from the old
laptop's `app/` folder into the new one's `app/` folder via **USB drive**
(simplest — don't email/cloud-share these, they're live credentials):
- `config.toml`
- `credentials.json`
- `token.json`

Paths inside `config.toml` are relative, so nothing needs editing.

## 5. Test it before scheduling anything
```powershell
.venv\Scripts\python -m budgettracker.main --dry-run
```
Should print parsed transactions with no errors. Then a real pass:
```powershell
.venv\Scripts\python -m budgettracker.main
```
Confirm a row lands in the Sheet (or "no new transactions" if nothing's arrived
since the last run on the old machine).

## 6. Schedule it — Task Scheduler (Windows' equivalent of cron)
1. Open **Task Scheduler** → *Create Task* (not "Basic Task" — you need the
   extra tabs).
2. **General tab**: name it `budget-tracker`; select **"Run whether user is
   logged on or not"**; check **"Run with highest privileges"**.
3. **Triggers tab** → New: *Begin the task: On a schedule* → Daily, recur every
   1 day → check **"Repeat task every: 15 minutes"**, for a duration of
   **"Indefinitely"**.
4. **Actions tab** → New → Program/script:
   ```
   C:\BudgetTracker\app\.venv\Scripts\python.exe
   ```
   Add arguments: `-m budgettracker.main`
   Start in: `C:\BudgetTracker\app`
5. **Conditions tab**: leaving "Start the task only if the computer is on AC
   power" checked is fine since it'll stay plugged in; also check **"Wake the
   computer to run this task"**.
6. **Settings tab**: check **"If the task is already running, then the
   following rule applies"** → **"Do not start a new instance"** (this is the
   `flock` equivalent from the Linux setup — Windows has no flock).

## 7. Stop the laptop from sleeping
Task Scheduler can't fire during sleep:
- **Settings → System → Power & battery → Screen and sleep** → set both
  "sleep" dropdowns (on battery / plugged in) to **Never**.
- **Control Panel → Power Options → Choose what closing the lid does** → set
  "When I close the lid" (plugged in) to **Do nothing**.
- Keep it on AC power at all times, lid can stay closed.

## 8. Verify end-to-end
- Check **Task Scheduler → your task → History tab** for a run every 15 min.
- Tail the log in PowerShell:
  ```powershell
  Get-Content C:\BudgetTracker\app\tracker.log -Wait -Tail 20
  ```
- Swipe a card → Telegram alert should arrive within 15 min.

## 9. Retire the old Lenovo's cron
Once the new machine has run cleanly for a day or so, SSH into the old Linux
Lenovo and remove its crontab entry (`crontab -e`, delete the line) so you
don't get duplicate Telegram alerts from both machines racing on the same
emails.

## After this is done
Update `DEPLOYMENT.md` and `TASKS.md` to reflect the new host, and close out
the migration task there.
