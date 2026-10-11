# inject_window.ps1 -- wake a GUI harness by OS-level input injection (ASCII only)
#
# WHY THIS EXISTS
#   Some harnesses expose neither an HTTP API (wakeup_method=http_api) nor a CLI we can
#   spawn (cli_acp), and they do not poll a mailbox (file_poll/pending_poll). For those,
#   the only remaining way to "wake" the session is the "mouse wake" the boss described:
#   bring its window forward and type/paste the message into it.
#
# *** NO REVERSE ENGINEERING IS INVOLVED. ***
#   This works purely through documented Windows APIs that apply to ANY window:
#     EnumWindows / GetWindowThreadProcessId  -> find the app's top-level windows
#     ShowWindowAsync + SetForegroundWindow   -> raise it
#     clipboard + Ctrl+V  (or SendKeys)       -> put the message in
#     VK_MENU (Alt) keybd_event trick         -> defeat the foreground lock
#   Nothing here inspects the harness's file format, memory, or network protocol.
#   What IS per-application is a small "adapter card": which window to use, how to
#   focus its input box, and which key sends the message. See -List and the README.
#
# WHAT IT DOES NOT DO: it cannot guarantee the app *accepted* the input.
#   Success is decided by the PLATFORM RECEIVING A REPORT-BACK from the harness
#   (POST /api/harness/task-result), not by this script. The script only reports
#   "injected"; per the project rule "failure must speak", never claim more than that.
#
# MEASURED ON THIS MACHINE (2026-10-08, WinForms TextBox target, read back from the app):
#   -Method wmpaste    -> delivered EXACTLY, twice out of two runs        <== PREFER THIS
#   -Method clipboard  -> first run (no warm-up) delivered NOTHING; with the warm-up it
#                         delivered, but it depends on focus state        <== fallback
#   -Method sendkeys   -> systematically LOST THE FIRST CHARACTER ("WAKE-x" arrived as
#                         "AKE-x"); a longer focus delay did NOT help (900 ms still lost it),
#                         and the -SendKeysLeadPad compensation run delivered nothing at all.
#                         <== last resort only, calibrate per application
#   RAISING THE WINDOW worked reliably in all runs (SetForegroundWindow=True, is_foreground=True).
#
#   READ THIS AS A CEILING, NOT A VERDICT: keystroke injection into a window we do not
#   control is inherently state-dependent. The target above was a form launched in the
#   background and never user-activated -- an unusually hostile target. A real harness the
#   user has open and focused may behave better. Either way the design conclusion is the
#   same: pick the method per application (the "adapter card"), and let the PLATFORM'S
#   REPORT-BACK decide whether the wake actually took effect.
#
# ADAPTER CARD (the only per-application part -- still no reverse engineering):
#   1. which window:      -List, then pick by -ProcId or a distinctive -TitleLike
#   2. how it takes text: -Method wmpaste if a text control exists, else clipboard/sendkeys
#   3. which key sends:   -Enter (add more keys here if an app uses Ctrl+Enter)
#   4. did it work:       the harness reports back; without a report-back treat it as failed
#
# Usage:
#   powershell -File inject_window.ps1 -List                     # list candidate windows
#   powershell -File inject_window.ps1 -Pid 1234 -Text "hi" -Enter
#   powershell -File inject_window.ps1 -TitleLike "Notepad" -Text "hi" -Enter
#   powershell -File inject_window.ps1 -Pid 1234 -Text "hi" -Method sendkeys
#   powershell -File inject_window.ps1 -Pid 1234 -Verify         # read back (standard Edit only)

param(
    [int]$TargetPid = 0,
    [string]$TitleLike = "",
    [string]$Text = "",
    [switch]$Enter,
    [ValidateSet("clipboard", "sendkeys", "wmpaste")]
    [string]$Method = "clipboard",
    [switch]$List,
    [switch]$Verify,
    [switch]$SendKeysLeadPad,
    [int]$FocusDelayMs = 350,
    [int]$PasteDelayMs = 450
)

$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinInj {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder s, int nMaxCount);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, StringBuilder s, int nMaxCount);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string cls, string title);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
}
"@

$VK_MENU = 0x12
$VK_CONTROL = 0x11
$VK_V = 0x56
$KEYUP = 0x2
$SW_RESTORE = 9

function Get-Windows {
    $found = New-Object System.Collections.ArrayList
    $cb = [WinInj+EnumWindowsProc]{
        param($hWnd, $lParam)
        $len = [WinInj]::GetWindowTextLength($hWnd)
        $sb = New-Object System.Text.StringBuilder ($len + 2)
        [void][WinInj]::GetWindowText($hWnd, $sb, $sb.Capacity)
        $cls = New-Object System.Text.StringBuilder 256
        [void][WinInj]::GetClassName($hWnd, $cls, $cls.Capacity)
        $opid = 0
        [void][WinInj]::GetWindowThreadProcessId($hWnd, [ref]$opid)
        if ([WinInj]::IsWindowVisible($hWnd)) {
            [void]$found.Add([pscustomobject]@{
                hwnd = [int64]$hWnd
                pid = $opid
                title = $sb.ToString()
                cls = $cls.ToString()
            })
        }
        return $true
    }
    [void][WinInj]::EnumWindows($cb, [IntPtr]::Zero)
    return $found
}

function Get-PidWindows([int]$targetPid) {
    return (Get-Windows | Where-Object { $_.pid -eq $targetPid })
}

function Focus-Window([int64]$hwnd) {
    # Foreground lock workaround: tap Alt so this process is allowed to steal foreground.
    # (Same trick the platform already uses for its own window-raise path.)
    [WinInj]::keybd_event($VK_MENU, 0, 0, [UIntPtr]::Zero)
    [WinInj]::keybd_event($VK_MENU, 0, $KEYUP, [UIntPtr]::Zero)
    [void][WinInj]::ShowWindowAsync([IntPtr]$hwnd, $SW_RESTORE)
    [void][WinInj]::BringWindowToTop([IntPtr]$hwnd)
    $ok = [WinInj]::SetForegroundWindow([IntPtr]$hwnd)
    Start-Sleep -Milliseconds $FocusDelayMs
    $fg = [WinInj]::GetForegroundWindow()
    return @{ requested = $ok; is_foreground = ([int64]$fg -eq $hwnd); foreground = [int64]$fg }
}

function Send-CtrlV {
    [WinInj]::keybd_event($VK_CONTROL, 0, 0, [UIntPtr]::Zero)
    [WinInj]::keybd_event($VK_V, 0, 0, [UIntPtr]::Zero)
    [WinInj]::keybd_event($VK_V, 0, $KEYUP, [UIntPtr]::Zero)
    [WinInj]::keybd_event($VK_CONTROL, 0, $KEYUP, [UIntPtr]::Zero)
}

function Send-Enter {
    [WinInj]::keybd_event(0x0D, 0, 0, [UIntPtr]::Zero)
    [WinInj]::keybd_event(0x0D, 0, $KEYUP, [UIntPtr]::Zero)
}

function Get-TextControl([int64]$hwnd) {
    # Find a text-control child WITHOUT matching class by equality:
    # WinForms boxes are "WindowsForms10.EDIT.app.0.*", RichEdit is "RICHEDIT50W".
    $kids = New-Object System.Collections.ArrayList
    $cb = [WinInj+EnumWindowsProc]{
        param($hWnd, $lParam)
        $cls = New-Object System.Text.StringBuilder 256
        [void][WinInj]::GetClassName($hWnd, $cls, $cls.Capacity)
        [void]$kids.Add([pscustomobject]@{ hwnd = [int64]$hWnd; cls = $cls.ToString() })
        return $true
    }
    [void][WinInj]::EnumChildWindows([IntPtr]$hwnd, $cb, [IntPtr]::Zero)
    return ($kids | Where-Object { $_.cls -match "(?i)edit" } | Select-Object -First 1)
}

function Invoke-WarmUp {
    # Tap Shift (produces no character) so the target's input queue / focus has settled.
    # Without this the FIRST injected character is regularly swallowed: verified
    # 2026-10-08 -- SendKeys delivered "AKE-KEYS-2" for input "WAKE-KEYS-2".
    [WinInj]::keybd_event(0x10, 0, 0, [UIntPtr]::Zero)
    [WinInj]::keybd_event(0x10, 0, $KEYUP, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 150
}

# ---------------- list mode ----------------
if ($List) {
    $rows = Get-Windows
    if ($TargetPid -gt 0) { $rows = $rows | Where-Object { $_.pid -eq $TargetPid } }
    if ($TitleLike) { $rows = $rows | Where-Object { $_.title -like ("*" + $TitleLike + "*") } }
    Write-Output ("visible top-level windows: " + @($rows).Count)
    foreach ($r in ($rows | Sort-Object pid)) {
        Write-Output ("  hwnd={0,-12} pid={1,-7} class={2,-28} title={3}" -f $r.hwnd, $r.pid, $r.cls, $r.title)
    }
    exit 0
}

# ---------------- resolve target ----------------
$targets = @()
if ($TargetPid -gt 0) {
    $targets = @(Get-PidWindows $TargetPid | Sort-Object -Property @{Expression = { if ($_.title) { 0 } else { 1 } } })
} elseif ($TitleLike) {
    $targets = @(Get-Windows | Where-Object { $_.title -like ("*" + $TitleLike + "*") })
}
if (@($targets).Count -eq 0) {
    Write-Output "[FAIL] no matching window (pass -Pid or -TitleLike; use -List to see what exists)"
    exit 3
}
$win = $targets[0]
Write-Output ("[target] hwnd={0} pid={1} class={2} title={3}" -f $win.hwnd, $win.pid, $win.cls, $win.title)

# ---------------- focus ----------------
$f = Focus-Window $win.hwnd
Write-Output ("[focus] SetForegroundWindow={0} is_foreground={1}" -f $f.requested, $f.is_foreground)
if (-not $f.is_foreground) {
    Write-Output "[WARN] window did not come to the foreground."
    Write-Output "       Likely causes: (a) target runs ELEVATED and we do not -> UIPI blocks input;"
    Write-Output "                      (b) another foreground lock; retry, or run this script elevated."
}

# ---------------- inject ----------------
if ($Text) {
    Invoke-WarmUp
    if ($Method -eq "wmpaste") {
        $tc = Get-TextControl $win.hwnd
        if (-not $tc) {
            Write-Output "[FAIL] -Method wmpaste needs a text-control child; none found."
            Write-Output "       Use -Method sendkeys (works on the focused control instead)."
            exit 3
        }
        Set-Clipboard -Value $Text
        Start-Sleep -Milliseconds 150
        [void][WinInj]::SendMessage([IntPtr]$tc.hwnd, 0x0302, [IntPtr]::Zero, [IntPtr]::Zero)  # WM_PASTE
        Write-Output ("[inject] WM_PASTE -> class={0}  ({1} chars)" -f $tc.cls, $Text.Length)
    } elseif ($Method -eq "clipboard") {
        Set-Clipboard -Value $Text
        Start-Sleep -Milliseconds 150
        Send-CtrlV
        Write-Output ("[inject] clipboard + Ctrl+V  ({0} chars)" -f $Text.Length)
    } else {
        Add-Type -AssemblyName System.Windows.Forms
        if ($SendKeysLeadPad) {
            # MEASURED (2026-10-08): SendKeys systematically eats the FIRST character
            # ("WAKE-slow" arrived as "AKE-slow"), and a longer focus delay does NOT fix it
            # (900 ms still lost it). The loss is deterministic, so pad with one throwaway
            # keystroke: it gets eaten instead of the real text. Only enable after you have
            # confirmed the loss on THAT application -- otherwise the pad itself lands.
            [System.Windows.Forms.SendKeys]::SendWait(" ")
            Start-Sleep -Milliseconds 120
        }
        [System.Windows.Forms.SendKeys]::SendWait($Text)
        Write-Output ("[inject] SendKeys  ({0} chars{1})" -f $Text.Length, $(if ($SendKeysLeadPad) { ", lead-padded" } else { "" }))
    }
    Start-Sleep -Milliseconds $PasteDelayMs
}
if ($Enter) {
    Send-Enter
    Write-Output "[inject] Enter"
    Start-Sleep -Milliseconds 200
}

# ---------------- optional read-back (standard text controls only) ----------------
if ($Verify) {
    $edit = Get-TextControl $win.hwnd
    if (-not $edit) {
        $kids = New-Object System.Collections.ArrayList
        $cb3 = [WinInj+EnumWindowsProc]{
            param($hWnd, $lParam)
            $cls = New-Object System.Text.StringBuilder 256
            [void][WinInj]::GetClassName($hWnd, $cls, $cls.Capacity)
            [void]$kids.Add($cls.ToString())
            return $true
        }
        [void][WinInj]::EnumChildWindows([IntPtr]$win.hwnd, $cb3, [IntPtr]::Zero)
        Write-Output ("[verify] no text-control child found among {0} child window(s)." -f @($kids).Count)
        Write-Output "         Classes seen: $(@($kids | Select-Object -Unique) -join ', ')"
        Write-Output "         Expected for Electron/Qt/custom-rendered apps; judge success by report-back."
    } else {
        $len = [WinInj]::GetWindowTextLength([IntPtr]$edit.hwnd)
        $sb = New-Object System.Text.StringBuilder ($len + 2)
        [void][WinInj]::GetWindowText([IntPtr]$edit.hwnd, $sb, $sb.Capacity)
        $got = $sb.ToString()
        $expected = if ($Text) { $Text } else { "" }
        $ok = $got.Contains($expected)
        Write-Output ("[verify] control class={0} text length={1} contains_expected={2}" -f $edit.cls, $got.Length, $ok)
        if (-not $ok) { exit 4 }
    }
}

Write-Output "[done] injected. NOTE: this only says *we sent input* --"
Write-Output "       'awake' is decided by the platform receiving the harness's report-back."
exit 0
