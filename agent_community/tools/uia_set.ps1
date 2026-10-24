# UI Automation 投递（Windows，零第三方依赖：用系统自带的 UIAutomationClient）
#
# 老板 2026-10-23：「只能浏览器吗」—— 不是。CDP 只管 Chromium/Electron；
# 原生 Win32/WinForms/WPF/UWP/Office 这类窗口，走 **Windows UI Automation**：
# 找到可编辑元素（ValuePattern / TextPattern）直接设值，再**读回核对**。
#
# 与"模拟鼠标键盘"的区别：UIA 是**无障碍接口**（应用主动暴露的语义），
# 不需要抢焦点、不需要点坐标，也不会把字打到别的程序里。
#
# 用法（Python 侧由 platform/uia_delivery.py 调用）：
#   powershell -NoProfile -File uia_set.ps1 -TitleLike "标题片段" [-Text "要写入的文本"] [-List]
# 输出（机器可读，一行一条）：
#   [uia] ok=True channel=uia target="标题" element="Edit" value_len=12 readback=ok
#   [uia] ok=False why=no-editable-element
# 退出码：0 成功 / 3 没找到窗口 / 4 找到窗口但没有可编辑元素 / 5 写入后读回不一致 / 6 其它错误

param(
    [string]$TitleLike = "",
    [int]$TargetPid = 0,
    [string]$Text = "",
    # 优先用**窗口句柄**定位（平台侧本来就知道 hwnd）：比在 UIA 全树里按标题搜快一个数量级，
    # 也不会因为 UWP/Electron 的包装层而搜不到（实测：Win11 记事本按标题搜不到，按 hwnd 能拿到）。
    [int64]$Hwnd = 0,
    [switch]$List
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase

$AE = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]

function Get-TargetWindow {
    if ($Hwnd -gt 0) {
        try { return ($AE::FromHandle([IntPtr]::new($Hwnd))) } catch { return $null }
    }
    $root = $AE::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, [System.Windows.Automation.ControlType]::Window)
    $wins = $root.FindAll($TS::Children, $cond)
    $best = $null
    $bestArea = -1
    foreach ($w in $wins) {
        try {
            $name = $w.Current.Name
            $pid = $w.Current.ProcessId
        } catch { continue }
        if ($TargetPid -gt 0 -and $pid -ne $TargetPid) { continue }
        if ($TitleLike -and ($name -notlike ("*" + $TitleLike + "*"))) { continue }
        # 同名窗口里取**面积最大**的（主窗口），与注入脚本同一口径
        $r = $w.Current.BoundingRectangle
        $area = [double]$r.Width * [double]$r.Height
        if ($area -gt $bestArea) { $bestArea = $area; $best = $w }
    }
    return $best
}

function Get-EditableElements([System.Windows.Automation.AutomationElement]$win) {
    $out = New-Object System.Collections.ArrayList
    # ★按**能力**找，不按 ControlType 找（2026-10-23 实测教训）：
    #   WinForms 的 TextBox 是"老式 Win32 EDIT 经 MSAA 代理"，ControlType 未必是 Edit；
    #   Win11 记事本暴露的是 Document。只认 Edit/Document 会两个都漏。
    #   判定标准就一条：**能用 ValuePattern 写（非只读）或能用 TextPattern 读**。
    $all = $win.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    $n = 0
    foreach ($e in $all) {
        $n++
        if ($n -gt 4000) { break }          # 大树（IDE/浏览器）最多看 4000 个，别卡死
        try {
            $r = $e.Current.BoundingRectangle
            if ($r.Width -le 0 -or $r.Height -le 0) { continue }
            if (-not $e.Current.IsEnabled) { continue }
            $vp = $null
            try { $vp = $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern) } catch {}
            if ($null -ne $vp) {
                $ro = $false
                try { $ro = $vp.Current.IsReadOnly } catch {}
                if ($ro) { $vp = $null }
            }
            $tp = $null
            if ($null -eq $vp) {
                try { $tp = $e.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern) } catch {}
            }
            if ($null -eq $vp -and $null -eq $tp) { continue }
            [void]$out.Add([pscustomobject]@{
                element = $e; name = $e.Current.Name
                ct = $e.Current.ControlType.ProgrammaticName
                value = $vp; text = $tp
                area = [double]$r.Width * [double]$r.Height
            })
        } catch { continue }
    }
    # 面积最大的那个最可能是"主输入框"（聊天框/正文区）
    return ($out | Sort-Object -Property area -Descending)
}

$win = Get-TargetWindow
if ($null -eq $win) {
    Write-Output "[uia] ok=False why=no-window title=$TitleLike pid=$TargetPid"
    exit 3
}
$title = ""
try { $title = $win.Current.Name } catch {}
$els = @(Get-EditableElements $win)

if ($List) {
    $i = 0
    foreach ($e in $els) {
        Write-Output ("[uia] #{0} ct={1} name={2} area={3}" -f $i, $e.ct, ($e.name -replace "`n", " "), [int]$e.area)
        $i++
    }
    exit 0
}

if ($els.Count -eq 0) {
    Write-Output "[uia] ok=False why=no-editable-element target=`"$title`""
    Write-Output "       （这个窗口没有暴露可编辑元素 —— UIA 走不通时可用 CDP（浏览器类）或剪贴板半自动档）"
    exit 4
}

$el = $els[0]
$before = ""
if ($null -ne $el.value) {
    try { $before = $el.value.Current.Value } catch { $before = "" }
} elseif ($null -ne $el.text) {
    try { $before = $el.text.DocumentRange.GetText(-1) } catch { $before = "" }
}

if (-not $Text) {
    Write-Output ("[uia] ok=True channel=uia target=`"{0}`" element={1} value_len={2} (只读查询，未写入)" -f $title, $el.ct, $before.Length)
    exit 0
}

$written = $false
if ($null -ne $el.value) {
    try { $el.value.SetValue($Text); $written = $true } catch { $written = $false }
}
if (-not $written) {
    # ValuePattern 不支持写入时，退回"聚焦 + SendKeys"（仍是 UIA 语义，不点坐标）
    try {
        $el.element.SetFocus()
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.SendKeys]::SendWait($Text -replace "([+^%~(){}])", "{$1}")
        $written = $true
    } catch {
        Write-Output ("[uia] ok=False why=write-failed {0}" -f $_.Exception.Message)
        exit 6
    }
}

Start-Sleep -Milliseconds 180
$after = ""
if ($null -ne $el.value) {
    try { $after = $el.value.Current.Value } catch { $after = "" }
} elseif ($null -ne $el.text) {
    try { $after = $el.text.DocumentRange.GetText(-1) } catch { $after = "" }
}
$ok = $after.Contains($Text)
Write-Output ("[uia] ok={0} channel=uia target=`"{1}`" element={2} value_len={3} readback={4}" -f $ok, $title, $el.ct, $after.Length, $(if ($ok) { "ok" } else { "mismatch" }))
if (-not $ok) {
    Write-Output ("       写入前 {0} 字 → 写入后 {1} 字；期望内容没出现在读回里" -f $before.Length, $after.Length)
    exit 5
}
exit 0
