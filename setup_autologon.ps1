<#
  NZB Command Center - start everything when the PC turns on, without you logging in.

  Right-click > Run with PowerShell (it asks for administrator rights once). It:
    1. downloads Autologon from Microsoft Sysinternals (a small, free Microsoft tool) and checks
       that it is signed by Microsoft before running it,
    2. opens Autologon's own window: check your user name, type your Windows password there and
       click Enable. (If you sign in with a Microsoft account, use that account's password, not
       your PIN.) Autologon keeps the password encrypted in Windows; this script never sees it,
    3. checks that automatic login is now on,
    4. adds the Task Scheduler task "Lock screen after automatic login": a few seconds after
       Windows logs you in, the screen locks. Your apps keep running; the PC still needs your
       PIN or password to use it.
  Result: when the PC turns on (or restarts after an update, or after a power cut with the UPS),
  Windows logs you in by itself, everything starts exactly as when you log in, and it locks.

  Safe to run again. To undo: run this with -Undo (turns automatic login off and removes the task).
#>
param([switch]$Elevated, [string]$ForUser, [switch]$Undo)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    if ($Elevated) { Write-Host "Administrator rights were not granted." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
    $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated -ForUser `"$env:USERDOMAIN\$env:USERNAME`""
    if ($Undo) { $a += " -Undo" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $a
    exit
}

& {
$ErrorActionPreference = "Stop"
$TaskName = "Lock screen after automatic login"
$Dir = Join-Path $env:ProgramData "NZB-CC\Autologon"
$Winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
function RegValue($path, $name) { try { return (Get-ItemProperty $path -Name $name -ErrorAction Stop).$name } catch { return $null } }
function AutoLogonOn { try { return ((Get-ItemProperty $Winlogon -Name AutoAdminLogon -ErrorAction Stop).AutoAdminLogon -eq "1") } catch { return $false } }
$user = if ($ForUser) { $ForUser } else { "$env:USERDOMAIN\$env:USERNAME" }
try {
    Say "Start everything when the PC turns on" Cyan
    Say "`"Good morning, Dave. I've been waiting for you.`"`n" DarkGray

    # --- Get Autologon from Microsoft (and check it really is Microsoft's) ---
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $exeName = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "Autologon64a.exe" } elseif ([Environment]::Is64BitOperatingSystem) { "Autologon64.exe" } else { "Autologon.exe" }
    $exe = Join-Path $Dir $exeName
    if (-not (Test-Path $exe)) {
        Say "Downloading Autologon from Microsoft Sysinternals..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $zip = Join-Path $Dir "AutoLogon.zip"
        Invoke-WebRequest "https://download.sysinternals.com/files/AutoLogon.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $Dir -Force
        Remove-Item $zip -Force
        if (-not (Test-Path $exe)) { throw "The download didn't contain $exeName. Get Autologon from https://learn.microsoft.com/sysinternals/downloads/autologon and run it yourself." }
    }
    $sig = Get-AuthenticodeSignature $exe
    if ($sig.Status -ne "Valid" -or "$($sig.SignerCertificate.Subject)" -notmatch "O=Microsoft Corporation") {
        Remove-Item $exe -Force -ErrorAction SilentlyContinue
        throw "$exeName isn't signed by Microsoft ($($sig.Status)), so it was deleted and nothing was changed."
    }
    Say "Autologon: signed by Microsoft - OK" Green

    if ($Undo) {
        Say "`nTurning automatic login off..." Cyan
        if (AutoLogonOn) {
            # Autologon's own way to turn it off: run it, untick/click Disable. Make sure it's off afterwards.
            Say "In the Autologon window, click Disable." Yellow
            Start-Process $exe -ArgumentList "/accepteula" -Wait
        }
        if (AutoLogonOn) { Set-ItemProperty $Winlogon -Name AutoAdminLogon -Value "0" }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Say "Done: Windows waits at the sign-in screen again, and the lock task is removed." Green
        return
    }

    # --- "Only Windows Hello sign-in" stops automatic login for Microsoft accounts ---
    $pl = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device"
    $plv = RegValue $pl "DevicePasswordLessBuildVersion"
    if ($plv -eq 2) {
        Say "`nWindows is set to 'only allow Windows Hello sign-in' (Settings > Accounts > Sign-in options)." Yellow
        Say "Automatic login can't work with that on. Your PIN keeps working either way."
        if ((Read-Host "Turn that option off? (Y/N)") -match '^[Yy]') {
            Set-ItemProperty $pl -Name DevicePasswordLessBuildVersion -Value 0 -Type DWord
            Say "Turned off." Green
        } else { Say "Left on. Automatic login may not work until it's off." Yellow }
    }

    # --- Autologon's window: the password is typed there, never here ---
    Say "`nIn the Autologon window that opens now:" Cyan
    Say "  1. check the Username (and Domain: your PC name, or MicrosoftAccount for a Microsoft account),"
    Say "  2. type your Windows password (a Microsoft account's password, not your PIN),"
    Say "  3. click Enable, then OK."
    Start-Process $exe -ArgumentList "/accepteula" -Wait
    if (-not (AutoLogonOn)) { throw "Automatic login isn't on (the Autologon window was closed without Enable). Nothing else was changed. Run this again to retry." }
    $who = RegValue $Winlogon "DefaultUserName"; if (-not $who) { $who = "?" }
    Say "Automatic login is on, as $who." Green

    # --- Lock the screen a few seconds after logging in ---
    # rundll32 user32.dll,LockWorkStation is Windows' own "lock" (the same as Windows+L).
    $action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\rundll32.exe" -Argument "user32.dll,LockWorkStation"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = "PT8S"
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
        -Description "Locks the screen right after Windows logs $user in by itself (automatic login), so everything starts but the PC still needs your PIN." -Force | Out-Null
    Say "Task '$TaskName' created: the screen locks 8 seconds after you're logged in." Green

    Say "`nAll set. Restart the PC to try it: it should log in by itself, start everything, and lock." Cyan
    Say "(If you ever sign out and back in by hand, it locks once then too - just type your PIN.)" DarkGray
    Say "To undo: run this script with -Undo." DarkGray
} catch {
    Say "`n$($_.Exception.Message)" Red
}
}
Read-Host "`nPress Enter to close"
