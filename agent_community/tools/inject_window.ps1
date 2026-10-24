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
    # 按**窗口句柄**精确定位（平台侧本来就有 hwnd；比按 pid/标题更不会认错窗口）
    [int64]$Hwnd = 0,
    [string]$TitleLike = "",
    [string]$Text = "",
    [switch]$Enter,
    [ValidateSet("clipboard", "sendkeys", "wmpaste")]
    [string]$Method = "clipboard",
    [switch]$List,
    [switch]$Verify,
    # ★2026-10-23：只做"把它拉到最上层"这一步，**不投递任何输入**。
    #   用途：① 排障（"到底能不能置顶"要和"能不能输入"分开看）；
    #        ② 界面上可以做成"先把它叫到前面"的按钮（比盲投安全）。
    [switch]$FocusOnly,
    [switch]$SendKeysLeadPad,
    # 只做"读回核对"，**不投递任何输入**：期望目标文本框里含有这段文字。
    # 用于"表层交互"（模拟鼠标键盘输入）之后的真假判定 —— 送进去了才算成。
    [string]$ExpectText = "",
    [int]$FocusDelayMs = 350,
    [int]$PasteDelayMs = 450
)

$ErrorActionPreference = "Stop"

# ★2026-10-20 修（老板真机报"探测到的窗口名是乱码"）：
#   Python 侧是按 **UTF-8** 读本脚本 stdout 的，而 Windows PowerShell 5.1 默认用 OEM/ANSI
#   （中文机器上是 GBK）往控制台写 ⇒ 中文窗口标题到了 Python 那边变成一串 `◆`/替换字符，
#   界面上就是"扫描到的窗口名全是方块"。
#   **一处真相**：输出编码在脚本这端钉死 UTF-8，别让调用方去猜。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinInj {
    // 窗口矩形（GetWindowRect 用）——2026-10-23 加：用来挑"面积最大的那个窗口"（主窗口）
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
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
    // ★2026-10-23：读控件文字要**用 WM_GETTEXT**，别用 GetWindowText。
    //   实测：WinForms 的 TextBox 是"被 WinForms 子类化的 EDIT"，`GetWindowText` 对它返回**空**
    //   （文字在 WinForms 自己的状态里）⇒ 读回核对永远 length=0，把"其实进去了"误判成"没进去"。
    //   WM_GETTEXT 走控件自己的消息处理，被 subclass 的控件也会如实返回。
    // ⚠️ 必须写 EntryPoint：方法名故意叫 SendMessageS（不与上面那个同名），但真正导出的只有 SendMessageW。
    [DllImport("user32.dll", CharSet=CharSet.Unicode, EntryPoint="SendMessageW")] public static extern IntPtr SendMessageS(IntPtr hWnd, uint msg, IntPtr wParam, StringBuilder lParam);
    [DllImport("user32.dll", EntryPoint="GetWindowTextLengthW")] public static extern int GetWindowTextLengthW(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
    // 2026-10-23: SwitchToThisWindow 是"切到那个窗口"的老 API；前台锁不给 SetForegroundWindow 时它常能成。
    [DllImport("user32.dll")] public static extern void SwitchToThisWindow(IntPtr hWnd, bool fAltTab);
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
            # ★2026-10-23：顺带记下窗口**面积**。同一标题常有多个窗口（主窗 + 小弹层/侧栏），
            #   原来直接取 `$targets[0]` ⇒ 可能落到 93×150 的小窗上（置顶/点击都点偏，用户看到"没反应"）。
            #   现在按面积从大到小挑（主窗口几乎总是最大的那个）。
            $wr = New-Object WinInj+RECT
            $area = 0
            if ([WinInj]::GetWindowRect($hWnd, [ref]$wr)) {
                $area = ([int64]($wr.Right - $wr.Left)) * ([int64]($wr.Bottom - $wr.Top))
            }
            [void]$found.Add([pscustomobject]@{
                hwnd = [int64]$hWnd
                pid = $opid
                title = $sb.ToString()
                cls = $cls.ToString()
                area = $area
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

    # ★2026-10-20 补（老板真机报"无法激活窗口、没调到最上层"）：
    #   只靠 SetForegroundWindow 会被"前台锁"顶回来（Electron/Chrome 尤其常见），
    #   于是文本发给了**后台**窗口，看起来"发出去了"其实没落到人眼前。
    #   兜底组合（Windows 上验证过的老办法）：
    #     ① 把本线程输入**临时附到当前前台线程**（AttachThreadInput）—— 才允许抢前台；
    #     ② SetForegroundWindow 再试一次；
    #     ③ SetWindowPos 抬到 TOP 并 SWP_SHOWWINDOW（保证"调到最上层"、不会被别的窗口压住）。
    #   全程 try/catch：任何一步失败都**如实**反映到 is_foreground，不假装成功。
    # ⚠️ 不要写 `[IntPtr][WinInj]::GetForegroundWindow()`：PowerShell 在
    #    `$ErrorActionPreference="Stop"` 下对 IntPtr 强转偶尔拿到 $null（真机实测踩到），
    #    直接用返回值 + `[int64]` 比较最稳。
    $fgObj = [WinInj]::GetForegroundWindow()
    $fgVal = 0
    if ($null -ne $fgObj) { $fgVal = [int64]$fgObj }
    if ($fgVal -ne $hwnd) {
        $curThread = [WinInj]::GetCurrentThreadId()
        [uint32]$pidTmp = 0
        $fgThread = 0
        try {
            if ($fgVal -ne 0) { $fgThread = [WinInj]::GetWindowThreadProcessId($fgObj, [ref]$pidTmp) }
        } catch {
            # 前台窗口句柄拿不到（0/无效）时**不阻断**：直接走下面的 SetForegroundWindow 重试
            $fgThread = 0
        }
        $attached = $false
        try {
            if ($fgThread -ne 0 -and $fgThread -ne $curThread) {
                $attached = [WinInj]::AttachThreadInput($curThread, $fgThread, $true)
            }
            [void][WinInj]::ShowWindowAsync([IntPtr]$hwnd, $SW_RESTORE)
            [void][WinInj]::SetForegroundWindow([IntPtr]$hwnd)
            # HWND_TOP=0；SWP_NOMOVE|SWP_NOSIZE|SWP_SHOWWINDOW = 0x0001|0x0002|0x0040
            [void][WinInj]::SetWindowPos([IntPtr]$hwnd, [IntPtr]::Zero, 0, 0, 0, 0, 0x0043)
            Start-Sleep -Milliseconds 120
        } catch {
            Write-Output ("[WARN] foreground retry failed: {0}" -f $_.Exception.Message)
        } finally {
            if ($attached) { [void][WinInj]::AttachThreadInput($curThread, $fgThread, $false) }
        }
        $ok = [WinInj]::SetForegroundWindow([IntPtr]$hwnd)
        Start-Sleep -Milliseconds 80
    }

    $fgNow = [int64][WinInj]::GetForegroundWindow()
    # ★2026-10-23：前台锁有时"这一次就是不肯给"（实测：连续调用时第二次常被顶回来）。
    #   重试 3 次、每次重新点一下 Alt 拿许可 —— 比一次失败就判死可靠得多。
    for ($try = 1; $try -le 3 -and $fgNow -ne $hwnd; $try++) {
        [WinInj]::SwitchToThisWindow([IntPtr]$hwnd, $true)
        [void][WinInj]::SetForegroundWindow([IntPtr]$hwnd)
        [WinInj]::keybd_event($VK_MENU, 0, 0, [UIntPtr]::Zero)
        [WinInj]::keybd_event($VK_MENU, 0, $KEYUP, [UIntPtr]::Zero)
        [void][WinInj]::ShowWindowAsync([IntPtr]$hwnd, $SW_RESTORE)
        [void][WinInj]::BringWindowToTop([IntPtr]$hwnd)
        [void][WinInj]::SetWindowPos([IntPtr]$hwnd, [IntPtr]::Zero, 0, 0, 0, 0, 0x0043)
        [void][WinInj]::SetForegroundWindow([IntPtr]$hwnd)
        Start-Sleep -Milliseconds 150
        $fgNow = [int64][WinInj]::GetForegroundWindow()
        if ($fgNow -eq $hwnd) { Write-Output ("[focus] 第 {0} 次重试成功（SwitchToThisWindow）" -f $try); break }
        if ($try -eq 2) {
            # 最后一招：最小化 + 还原 —— 系统会把它当作"用户要看它"，通常能拿到前台
            [void][WinInj]::ShowWindowAsync([IntPtr]$hwnd, 6)
            Start-Sleep -Milliseconds 120
            [void][WinInj]::ShowWindowAsync([IntPtr]$hwnd, $SW_RESTORE)
            [void][WinInj]::SetForegroundWindow([IntPtr]$hwnd)
            Start-Sleep -Milliseconds 180
            $fgNow = [int64][WinInj]::GetForegroundWindow()
            if ($fgNow -eq $hwnd) { Write-Output ("[focus] 第 {0} 次：最小化+还原成功" -f $try); break }
        }
    }
    return @{ requested = $ok; is_foreground = ($fgNow -eq $hwnd); foreground = $fgNow }
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

function Read-ControlText([int64]$hWnd) {
    # 2026-10-23: read via WM_GETTEXT (0x000D) instead of GetWindowText.
    # WinForms/RichEdit subclasses return "" from GetWindowText -> readback falsely red.
    $cap = 4096
    $sb = New-Object System.Text.StringBuilder $cap
    [void][WinInj]::SendMessageS([IntPtr]$hWnd, 0x000D, [IntPtr]$cap, $sb)
    return $sb.ToString()
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

function Set-ClipboardChecked([string]$value) {
    # ★2026-10-23：`Set-Clipboard` 失败是**非终止错误**（默认 $ErrorActionPreference='Continue'）——
    #   原来写失败也照样往下走：WM_PASTE / Ctrl+V 贴的是**旧内容或什么都没贴**，
    #   而脚本仍打印 `[inject] …`、退出码 0 ⇒ 上层判成"送出去了"（老板真机看到的现象：
    #   "只弹几个黑框、目标没反应，界面却说已兜底送到"）。
    #   实测失败形态：`ExternalException: Requested Clipboard operation did not succeed.`
    #   （剪贴板被别的程序短暂占用）。这里：① `-ErrorAction Stop` 让它可捕获；
    #   ② 失败**重试 3 次**（占用通常是瞬时的）；③ 写成功后**读回核对**，不一致也算失败。
    $lastWhy = ""
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Set-Clipboard -Value $value -ErrorAction Stop
            Start-Sleep -Milliseconds 80
            $back = ""
            $readbackOk = $false
            try {
                $back = (Get-Clipboard -Raw -ErrorAction Stop)
                $readbackOk = ($back -eq $value)
            } catch {
                # 读不了剪贴板（少见）= 没核对上，但**写入本身已成功**：如实标 readback=False
                $readbackOk = $false
            }
            if ($readbackOk) {
                return @{ ok = $true; why = ""; readback = $true }
            }
            # 能读但内容不一致 ⇒ 值得重试（可能被别的程序改写了）
            $lastWhy = "readback mismatch (got {0} chars, expected {1})" -f $back.Length, $value.Length
        } catch {
            $lastWhy = $_.Exception.Message
        }
        Start-Sleep -Milliseconds (120 * $attempt)
    }
    return @{ ok = $false; why = $lastWhy; readback = $false }
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
if ($Hwnd -gt 0) {
    $targets = @(Get-Windows | Where-Object { $_.hwnd -eq $Hwnd })
    if (@($targets).Count -eq 0) {
        # 可见但没有标题的窗口（或枚举口径不同）：直接构造一个目标，别因为"没标题"就找不到
        $sbT = New-Object System.Text.StringBuilder 512
        [void][WinInj]::GetWindowText($hWnd, $sbT, $sbT.Capacity)
        $sbC = New-Object System.Text.StringBuilder 256
        [void][WinInj]::GetClassName($hWnd, $sbC, $sbC.Capacity)
        $wr = New-Object WinInj+RECT
        $area = 0
        if ([WinInj]::GetWindowRect([IntPtr]$Hwnd, [ref]$wr)) {
            $area = ([int64]($wr.Right - $wr.Left)) * ([int64]($wr.Bottom - $wr.Top))
        }
        $targets = @([pscustomobject]@{ hwnd = [int64]$Hwnd; pid = 0
                                        title = $sbT.ToString(); cls = $sbC.ToString(); area = $area })
    }
} elseif ($TargetPid -gt 0) {
    $targets = @(Get-PidWindows $TargetPid | Sort-Object -Property @{Expression = { if ($_.title) { 0 } else { 1 } } })
} elseif ($TitleLike) {
    $targets = @(Get-Windows | Where-Object { $_.title -like ("*" + $TitleLike + "*") })
}
if (@($targets).Count -eq 0) {
    Write-Output "[FAIL] no matching window (pass -Pid or -TitleLike; use -List to see what exists)"
    exit 3
}
# ★2026-10-23：多窗口同名时**取面积最大的那个**（主窗口），不是"第一个"。
#   实测：Trae 的匹配结果里同时有主窗口和 93×150 的小弹层，取第一个就会置顶/点击小窗
#   ⇒ 用户看到"点了没反应"。
if (@($targets).Count -gt 1) {
    $targets = @($targets | Sort-Object -Property area -Descending)
    Write-Output ("[targets] 命中 {0} 个同名窗口，取面积最大的那个（其余略）" -f @($targets).Count)
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

# ---------------- focus-only（只置顶，不投递） ----------------
if ($FocusOnly) {
    # 排障/界面按钮用：把"能不能置顶"和"能不能输入"分开看。
    if ($f.is_foreground) {
        Write-Output "[focus-only] ok=True  is_foreground=True  （已在最上层，未投递任何输入）"
        exit 0
    }
    Write-Output "[focus-only] ok=False is_foreground=False （没能置顶，未投递任何输入）"
    Write-Output "             可能原因：目标以管理员身份运行（UIPI 挡住）、或另一个程序正抢前台锁；"
    Write-Output "             办法：手动点一下目标窗口，或用管理员权限重跑。"
    exit 7
}

# ---------------- inject ----------------
if ($Text) {
    Invoke-WarmUp
    # ★2026-10-23（老板真机报"点试投只弹几个黑框、目标没置顶也没输入"）：
    #   `clipboard` / `sendkeys` 是**键鼠注入**——它们把按键送到**当前前台窗口**。
    #   如果目标窗口没有被置顶，Ctrl+V 会**贴到前台那个别的窗口里去**（内容跑到别处，
    #   而我们还以为"送出去了"）。所以这两种方式**没有前台就不投递**，如实退出 6，
    #   由调用方告诉用户"先点一下目标窗口"。`wmpaste` 是消息式的（WM_PASTE 直接发给控件），
    #   不需要前台，所以不受这条限制。
    if (($Method -eq "clipboard" -or $Method -eq "sendkeys") -and -not $f.is_foreground) {
        Write-Output "[FAIL] target window is not in the foreground -> refuse to inject keystrokes."
        Write-Output "       Reason: Ctrl+V / SendKeys go to whatever window IS in front;"
        Write-Output "       pasting blind would drop the text into some other app."
        Write-Output "       Fix: click the target window once and retry, or run this script elevated"
        Write-Output "            (if the target runs elevated, UIPI blocks our input entirely)."
        exit 6
    }
    if ($Method -eq "wmpaste") {
        $tc = Get-TextControl $win.hwnd
        if (-not $tc) {
            Write-Output "[FAIL] -Method wmpaste needs a text-control child; none found."
            Write-Output "       Use -Method sendkeys (works on the focused control instead)."
            exit 3
        }
        # 剪贴板写不进去要**当场判死**（否则下一步 WM_PASTE 贴的是旧内容，表现为"什么都没发生"）
        $clip = Set-ClipboardChecked $Text
        if (-not $clip.ok) {
            Write-Output ("[FAIL] clipboard write failed: {0}" -f $clip.why)
            exit 5
        }
        Start-Sleep -Milliseconds 150
        [void][WinInj]::SendMessage([IntPtr]$tc.hwnd, 0x0302, [IntPtr]::Zero, [IntPtr]::Zero)  # WM_PASTE
        Write-Output ("[inject] WM_PASTE -> class={0}  ({1} chars)" -f $tc.cls, $Text.Length)
    } elseif ($Method -eq "clipboard") {
        # 同上：剪贴板失败 ⇒ exit 5；成功要**读回核对**（[clipboard] wrote=N readback=ok），
        # 免得"我们按了 Ctrl+V"被当成"内容到了"。
        $clip = Set-ClipboardChecked $Text
        if (-not $clip.ok) {
            Write-Output ("[FAIL] clipboard write failed: {0}" -f $clip.why)
            exit 5
        }
        $rb = "unavailable"
        if ($clip.readback) { $rb = "ok" }
        Write-Output ("[clipboard] wrote={0} readback={1}" -f $Text.Length, $rb)
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
if ($ExpectText) {
    # 只读回核对、不投递：表层交互（模拟鼠标键盘）之后用它判"到底进没进去"。
    $edit0 = Get-TextControl $win.hwnd
    if (-not $edit0) {
        Write-Output "[verify] no text-control child found（目标读不回来 ⇒ 只能如实说未核实）"
        exit 4
    }
    $got0 = Read-ControlText $edit0.hwnd
    $ok0 = $got0.Contains($ExpectText)
    Write-Output ("[verify] control class={0} text length={1} contains_expected={2}" -f $edit0.cls, $got0.Length, $ok0)
    if (-not $ok0) { exit 4 }
    exit 0
}
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
        $got = Read-ControlText $edit.hwnd
        $expected = if ($Text) { $Text } else { "" }
        $ok = $got.Contains($expected)
        Write-Output ("[verify] control class={0} text length={1} contains_expected={2}" -f $edit.cls, $got.Length, $ok)
        if (-not $ok) { exit 4 }
    }
}

Write-Output "[done] injected. NOTE: this only says *we sent input* --"
Write-Output "       'awake' is decided by the platform receiving the harness's report-back."
exit 0
