# メディアコンバーター アンインストーラ
#
# 「アンインストール.bat」から呼ばれる。
#
# 方針（johukku 製ツールで共通）
#   - このツール専用のもの（フォルダの中身・設定・一時ファイル）は、最初の確認でまとめて消す
#   - 他と共有しているもの（FFmpeg・yt-dlp）は 1 つずつ聞く。Enter は「残す」
#   - フォルダを丸ごとは消さない。配布物として知っているものだけを消し、空になったらフォルダも消す
#     （利用者が置いた動画や字幕を巻き込まないため）
#   - 最後の確認が済むまでは何も消さない
#
# 雛形は Whisper 字幕作成ツールの uninstall.ps1。「設定」の部分だけを書き換えてあり、
# それより下の共通の処理は同じ内容にそろえている（直すときは両方直す）。
# パスは環境変数から組み立てる。アプリ本体（Python 側）と同じ決め方にそろえるため。
#
# このファイルは UTF-8（BOM 付き）で保存すること。
# BOM が無いと Windows PowerShell 5.1 が日本語を読み違える。

param(
    [Parameter(Mandatory = $true)]
    [string] $ToolDir
)

# 想定外のエラーでも、何が起きたかを見せてから閉じる（bat 側は PowerShell が終わるとすぐ閉じる）
trap {
    Write-Host ''
    Write-Host ('  予期しないエラーで中止しました: ' + $_) -ForegroundColor Red
    Write-Host ''
    Write-Host -NoNewline 'Enter キーで閉じます。'
    [void][Console]::ReadLine()
    exit 1
}


# ======================================================================
#  設定
# ======================================================================

$ToolName = 'メディアコンバーター'

# このフォルダがツール本体だと判断する目印。どれか 1 組が全部そろっていればよい。
# （exe 版だけを配布している）
$Editions = @(
    @{ Name = 'exe'; Markers = @('メディアコンバーター.exe', '_internal') }
)

# 配布物として知っているもの。フォルダの中のこれ以外には触らない。
# _build\make_exe.py が、出来上がったフォルダの中身とこの一覧を照合している。
$KnownFiles = @(
    'メディアコンバーター.exe',
    'はじめにお読みください.txt',
    'アンインストール.bat',
    'uninstall.ps1',
    'LICENSE',
    'settings.json',
    'Thumbs.db',
    'desktop.ini'
)
# settings.json … 設定の保存先を作れなかったときの退避先
# Thumbs.db / desktop.ini … エクスプローラーが勝手に作る。これが残ってフォルダが消えないのを防ぐ

$KnownDirs = @(
    '_internal'       # exe の本体（PyInstaller）
)

# このツールだけが使う場所。最初の確認でまとめて消す。
$OwnPaths = @(
    @{ Label = '設定ファイル'; Path = (Join-Path $env:LOCALAPPDATA 'johukku\media-converter') }
)

# TEMP に残ることがあるもの（強制終了したときの作業フォルダと、FFmpeg の記録）。
# johukku-convert-* はこのツールだけが作る名前なので消してよい。
# FFmpeg を取得するときの作業フォルダ（johukku-ffmpeg-*）は他のツールと同じ名前で
# 区別できないので、ここでは消さない。
$OwnTempPatterns = @('johukku-convert-*')

# 他と共有しているもの。1 つずつ聞く。
$SharedItems = @(
    @{
        Label    = 'FFmpeg / yt-dlp'
        Path     = (Join-Path $env:LOCALAPPDATA 'johukku\bin')
        Editions = @('exe')
        Note     = @('動画・音声の変換に使います。',
                     'Whisper 字幕作成ツールやメディアダウンローダーと共有しています。')
    }
)

# 共有物を消したあと、空になっていれば消す親フォルダ
# （johukku\ の下には他のツールの設定もあるので、丸ごとは消さない）
$RemoveIfEmpty = @((Join-Path $env:LOCALAPPDATA 'johukku'))


# ======================================================================
#  ここから下は共通の処理（ふつうは書き換えなくてよい）
# ======================================================================

# ----------------------------------------------------------- 表示と入力

function Write-Rule([string] $Title) {
    Write-Host ''
    Write-Host '--------------------------------------------------'
    Write-Host (' ' + $Title)
    Write-Host '--------------------------------------------------'
}

function Read-YesNo([string] $Prompt) {
    Write-Host -NoNewline ($Prompt + ' [y/N]: ')
    $answer = [Console]::ReadLine()
    if ([Console]::IsInputRedirected) { Write-Host $answer }   # 流し込んだ入力も記録に残す
    if ($null -eq $answer) { return $false }
    return @('y', 'yes', 'ｙ', 'ｙｅｓ', 'はい') -contains $answer.Trim().ToLowerInvariant()
}

function Wait-Close {
    Write-Host ''
    Write-Host -NoNewline 'Enter キーで閉じます。'
    [void][Console]::ReadLine()
    Write-Host ''
}

function Stop-Here([string[]] $Reason) {
    Write-Host ''
    foreach ($line in $Reason) { Write-Host ('  ' + $line) -ForegroundColor Yellow }
    Write-Host ''
    Write-Host '  何も削除していません。'
    Wait-Close
    exit 1
}

function Get-DisplayWidth([string] $Text) {
    $width = 0
    foreach ($ch in $Text.ToCharArray()) {
        if ([int] $ch -ge 0x1100) { $width += 2 } else { $width += 1 }
    }
    return $width
}

function Write-Row([string] $Label, [double] $Bytes) {
    $pad = [Math]::Max(2, 34 - (Get-DisplayWidth $Label))
    Write-Host ('  ・' + $Label + (' ' * $pad) + (Format-Size $Bytes))
}

function Format-Size([double] $Bytes) {
    if ($Bytes -ge 1GB) { return ('{0:N1} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N0} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N0} KB' -f ($Bytes / 1KB)) }
    return ('{0:N0} バイト' -f $Bytes)
}

# ---------------------------------------------------------- ファイル操作

$Sizes = @{}

function Get-Size([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return [double] 0 }
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) { return [double] $item.Length }
    try {
        # 数 GB のフォルダでも 1 秒かからない
        return [double] (New-Object -ComObject Scripting.FileSystemObject).GetFolder($Path).Size
    } catch {
        $sum = (Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum
        if ($null -eq $sum) { return [double] 0 }
        return [double] $sum
    }
}

function Measure-Targets([string[]] $Targets) {
    $total = [double] 0
    foreach ($target in $Targets) {
        $size = Get-Size $target
        $script:Sizes[$target] = $size
        $total += $size
    }
    return $total
}

function Get-LongPath([string] $Path) {
    if ($Path.StartsWith('\\?\')) { return $Path }
    if ($Path.StartsWith('\\')) { return ('\\?\UNC\' + $Path.Substring(2)) }
    return ('\\?\' + $Path)
}

function Remove-Target([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        # rd はジャンクションの先をたどらず、読み取り専用のファイルや 260 文字を超えるパスも消せる。
        # Windows PowerShell 5.1 の Remove-Item -Recurse は、ジャンクションの先の中身まで消してしまう。
        cmd /c ('rd /s /q "' + (Get-LongPath $Path) + '"') 2>&1 | Out-Null
    } else {
        try {
            $item.IsReadOnly = $false
            Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
        } catch {
            # 消えたかどうかは下で確かめる
        }
    }
    return (-not (Test-Path -LiteralPath $Path))
}

function Test-EmptyDir([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    return (@(Get-ChildItem -LiteralPath $Path -Force).Count -eq 0)
}


# ================================================================ 本体

Write-Host '=================================================='
Write-Host ('   ' + $ToolName + '  アンインストール')
Write-Host '=================================================='

# ------------------------------------------------------ 対象フォルダの確認

$ToolDir = [IO.Path]::GetFullPath($ToolDir)
$DriveRoot = [IO.Path]::GetPathRoot($ToolDir).TrimEnd('\')
$ToolDir = $ToolDir.TrimEnd('\')

Write-Host ''
Write-Host '対象のフォルダ:'
Write-Host ('    ' + $ToolDir)

if (-not (Test-Path -LiteralPath $ToolDir -PathType Container)) {
    Stop-Here @('フォルダが見つかりません。')
}

# ドライブの直下やユーザーフォルダそのものでは、何があっても動かない
$Protected = @(
    $env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA, $env:TEMP, $env:WINDIR,
    $env:ProgramFiles, ${env:ProgramFiles(x86)},
    (Join-Path $env:USERPROFILE 'Desktop'),
    (Join-Path $env:USERPROFILE 'Documents'),
    (Join-Path $env:USERPROFILE 'Downloads')
) | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\') }

if (($ToolDir -eq $DriveRoot) -or ($Protected -contains $ToolDir)) {
    Stop-Here @('このフォルダでは実行できません。',
                'ツールを展開したフォルダの中から実行してください。')
}

if (Test-Path -LiteralPath (Join-Path $ToolDir '.git')) {
    Stop-Here @('開発用のフォルダ（.git がある）では実行しません。')
}

$Edition = $null
foreach ($candidate in $Editions) {
    $complete = $true
    foreach ($marker in $candidate.Markers) {
        if (-not (Test-Path -LiteralPath (Join-Path $ToolDir $marker))) { $complete = $false }
    }
    if ($complete) { $Edition = $candidate.Name; break }
}
if (-not $Edition) {
    Stop-Here @(('このフォルダは「' + $ToolName + '」のフォルダではないようです。'),
                'アンインストール.bat は、ツールを展開したフォルダの中から実行してください。')
}

# ------------------------------------------------------------ 使用中の確認

# 起動.bat から動かしている Python（.venv の中）や exe が残っていると、途中で消せなくなる。
# 自分自身と、自分を呼んだ cmd（コマンドラインにこのフォルダが入っている）は除く。
$Busy = @()
try {
    $me = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $PID)
    $ignore = @($PID, $me.ParentProcessId)
    $prefix = $ToolDir + '\'
    $Busy = @(Get-CimInstance Win32_Process | Where-Object {
        ($ignore -notcontains $_.ProcessId) -and (
            ($_.ExecutablePath -and
             $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.CommandLine -and
             ($_.CommandLine.IndexOf($prefix, [StringComparison]::OrdinalIgnoreCase) -ge 0)))
    })
} catch {
    # 調べられない環境では先に進む。消せなかったものは最後に知らせる
}
if ($Busy.Count -gt 0) {
    $names = (@($Busy | ForEach-Object { $_.Name }) | Sort-Object -Unique) -join ', '
    Stop-Here @(('ツールがまだ使用中です（' + $names + '）。'),
                'ツールの画面を閉じてから、もう一度実行してください。')
}

# ------------------------------------------------------ 消すものを集める

Write-Host ''
Write-Host '容量を調べています...'

$Known = @($KnownFiles + $KnownDirs)
$Plan = New-Object System.Collections.ArrayList

$folderTargets = @()
foreach ($name in $Known) {
    $path = Join-Path $ToolDir $name
    if (Test-Path -LiteralPath $path) { $folderTargets += $path }
}
[void]$Plan.Add(@{ Label = 'ツールのフォルダ'; Targets = $folderTargets;
                   Size = (Measure-Targets $folderTargets); IsToolFolder = $true })

foreach ($own in $OwnPaths) {
    if (Test-Path -LiteralPath $own.Path) {
        [void]$Plan.Add(@{ Label = $own.Label; Targets = @($own.Path);
                           Size = (Measure-Targets @($own.Path)) })
    }
}

$tempTargets = @()
foreach ($pattern in $OwnTempPatterns) {
    $tempTargets += @(Get-ChildItem -LiteralPath $env:TEMP -Filter $pattern -Force -ErrorAction SilentlyContinue |
                      ForEach-Object { $_.FullName })
}
if ($tempTargets.Count -gt 0) {
    [void]$Plan.Add(@{ Label = '作業用の一時ファイル'; Targets = $tempTargets;
                       Size = (Measure-Targets $tempTargets) })
}

$Others = @(Get-ChildItem -LiteralPath $ToolDir -Force | Where-Object { $Known -notcontains $_.Name })

Write-Host ''
Write-Host 'このツールを PC から削除します。'
Write-Host ''
foreach ($entry in $Plan) { Write-Row $entry.Label $entry.Size }
if (Test-Path -LiteralPath (Join-Path $ToolDir 'models')) {
    Write-Host '      （手渡しで置いた AI モデル＝ models フォルダも含みます）'
}
if ($Others.Count -gt 0) {
    Write-Host ''
    Write-Host ('  フォルダの中にある、ツール以外のファイル ' + $Others.Count + ' 個は残します。')
}
Write-Host ''
if (-not (Read-YesNo '削除を始めますか？')) {
    Write-Host ''
    Write-Host '  中止しました。何も削除していません。'
    Wait-Close
    exit 0
}

# ---------------------------------------------------------- 共有物を聞く

$Candidates = New-Object System.Collections.ArrayList
foreach ($item in $SharedItems) {
    if ($item.Editions -notcontains $Edition) { continue }
    if (-not (Test-Path -LiteralPath $item.Path)) { continue }
    if ($item.Files) {
        $targets = @($item.Files | ForEach-Object { Join-Path $item.Path $_ } |
                     Where-Object { Test-Path -LiteralPath $_ })
    } else {
        $targets = @($item.Path)
    }
    if ($targets.Count -eq 0) { continue }
    [void]$Candidates.Add(@{ Label = $item.Label; Path = $item.Path; Targets = $targets;
                             Note = $item.Note; Size = (Measure-Targets $targets);
                             CleanupDir = [bool] $item.Files })
}

$Chosen = New-Object System.Collections.ArrayList
if ($Candidates.Count -gt 0) {
    Write-Rule '他のソフトと共有しているもの'
    Write-Host '消しても、次に使うときにもう一度ダウンロードされるだけです。'
    Write-Host '迷ったら Enter（残す）で大丈夫です。'
    $number = 0
    foreach ($candidate in $Candidates) {
        $number++
        Write-Host ''
        Write-Host ('[{0}/{1}] {2}  {3}' -f $number, $Candidates.Count, $candidate.Label,
                    (Format-Size $candidate.Size))
        Write-Host ('      ' + $candidate.Path) -ForegroundColor DarkGray
        foreach ($line in $candidate.Note) { Write-Host ('      ' + $line) }
        if (Read-YesNo '      削除しますか？') { [void]$Chosen.Add($candidate) }
    }
}

# -------------------------------------------------------------- 最終確認

$All = @($Plan) + @($Chosen)
$Total = [double] 0

Write-Rule '次のものを削除します'
foreach ($entry in $All) {
    Write-Row $entry.Label $entry.Size
    $Total += $entry.Size
}
Write-Host ('  ' + ('-' * 46))
Write-Host ('    合計' + (' ' * 30) + (Format-Size $Total))
Write-Host ''
if (-not (Read-YesNo '本当に削除しますか？')) {
    Write-Host ''
    Write-Host '  中止しました。何も削除していません。'
    Wait-Close
    exit 0
}

# ------------------------------------------------------------------ 削除

Write-Host ''
Write-Host '削除しています...'

$Freed = [double] 0
$Failed = New-Object System.Collections.ArrayList

# ツールのフォルダは最後に消す（途中で止まっても、このファイルが残っていればやり直せる）
$Order = @($All | Where-Object { -not $_.IsToolFolder }) + @($All | Where-Object { $_.IsToolFolder })
foreach ($entry in $Order) {
    $complete = $true
    foreach ($target in $entry.Targets) {
        if (Remove-Target $target) {
            $Freed += $Sizes[$target]
        } else {
            $complete = $false
            [void]$Failed.Add($target)
        }
    }
    if ($entry.CleanupDir -and (Test-EmptyDir $entry.Path)) { [void](Remove-Target $entry.Path) }

    if ($complete) {
        Write-Host ('  ' + $entry.Label + ' ... 削除しました')
    } else {
        Write-Host ('  ' + $entry.Label + ' ... 一部を削除できませんでした') -ForegroundColor Yellow
    }
}

foreach ($dir in $RemoveIfEmpty) {
    if (Test-EmptyDir $dir) { [void](Remove-Target $dir) }
}

if (Test-EmptyDir $ToolDir) {
    if (-not (Remove-Target $ToolDir)) { [void]$Failed.Add($ToolDir) }
}

# ------------------------------------------------------------------ 結果

Write-Host ''
Write-Host '=================================================='
if ($Failed.Count -eq 0) {
    Write-Host ('   完了しました。約 ' + (Format-Size $Freed) + ' 空きました。') -ForegroundColor Green
} else {
    Write-Host ('   一部を削除できませんでした（約 ' + (Format-Size $Freed) + ' は空きました）。') -ForegroundColor Yellow
}
Write-Host '=================================================='

if ($Failed.Count -gt 0) {
    Write-Host ''
    Write-Host '次のものは削除できませんでした。'
    Write-Host 'ツールや、中のファイルを開いているソフトを閉じてから、もう一度実行してください。'
    Write-Host '（手で削除してもかまいません）'
    @($Failed) | Select-Object -First 10 | ForEach-Object { Write-Host ('    ' + $_) }
    if ($Failed.Count -gt 10) { Write-Host ('    ほか ' + ($Failed.Count - 10) + ' 個') }
}

if (Test-Path -LiteralPath $ToolDir) {
    $left = @(Get-ChildItem -LiteralPath $ToolDir -Force | Where-Object { $Known -notcontains $_.Name })
    if ($left.Count -gt 0) {
        Write-Host ''
        Write-Host 'フォルダの中にツール以外のファイルがあったので、フォルダは残しました。'
        Write-Host ('    ' + $ToolDir)
        $left | Select-Object -First 10 | ForEach-Object { Write-Host ('      ' + $_.Name) }
        if ($left.Count -gt 10) { Write-Host ('      ほか ' + ($left.Count - 10) + ' 個') }
    }
}

if ($Edition -eq 'zip') {
    Write-Host ''
    Write-Host 'Python 本体は削除していません。'
    Write-Host '不要なら Windows の「設定」→「アプリ」から削除してください。'
}

Wait-Close
if ($Failed.Count -gt 0) { exit 2 }
exit 0
