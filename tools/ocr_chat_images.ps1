# 把微信聊天截图（图片）转成带说话人的文本，喂给 run.py profile 用。
#
# 原理：调用 Windows 自带的 OCR 引擎（不需要联网、不需要装任何东西），
#       识别出每一行的文字和坐标，再按"对方在左、自己在右"的位置规律判断说话人。
#
# 用法（在项目目录里）：
#   powershell -ExecutionPolicy Bypass -File tools\ocr_chat_images.ps1 -Folder "D:\微信截图"
#   powershell -ExecutionPolicy Bypass -File tools\ocr_chat_images.ps1 -Folder "D:\微信截图" -SelfName 我 -OtherName 张三 -OutFile work\聊天记录.txt
#
# 注意：必须用 Windows 自带的 powershell.exe 运行（不要用 pwsh）。

param(
    [string]$Folder = "",
    [string[]]$Files = @(),
    [string]$ListFile = "",
    [string]$OutFile = "",
    [string]$SelfName = "我",
    [string]$OtherName = "对方",
    [double]$SelfThreshold = 0.55,
    [double]$OtherThreshold = 0.45,
    [double]$SpaceRatioThreshold = 0.20,
    [double]$CropTopPercent = 0.0,
    [double]$CropBottomPercent = 0.0,
    [double]$Scale = 1.0,
    [double]$DropRareRatio = 0.0,
    [switch]$PhoneLayout,
    [switch]$Invert,
    [int]$SliceHeight = 0,
    [string]$FixFile = "",
    [switch]$KeepLineBreaks
)

$ErrorActionPreference = "Stop"

# 手机截图的典型布局：顶部有状态栏+联系人标题栏，底部有输入框，字也比较小
if ($PhoneLayout) {
    if ($CropTopPercent -eq 0) { $CropTopPercent = 0.09 }
    if ($CropBottomPercent -eq 0) { $CropBottomPercent = 0.12 }
    if ($Scale -eq 1.0) { $Scale = 2.0 }
}

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime

[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($operation, $resultType) {
    $method = $asTaskGeneric.MakeGenericMethod($resultType)
    $task = $method.Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
    return $task.Result
}

$script:Engine = $null
try {
    [void][Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
    $chinese = New-Object Windows.Globalization.Language -ArgumentList "zh-Hans-CN"
    $script:Engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($chinese)
} catch {
    $script:Engine = $null
}
if ($null -eq $script:Engine) {
    $script:Engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $script:Engine) {
    Write-Host "找不到可用的 OCR 语言包，请在 设置 -> 时间和语言 -> 语言 里给中文加上"可选字体/OCR"功能。" -ForegroundColor Red
    exit 1
}

function Invoke-OcrImage([string]$filePath) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($filePath)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($script:Engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        return [pscustomobject]@{
            Result = $result
            Width  = $bitmap.PixelWidth
            Height = $bitmap.PixelHeight
        }
    } finally {
        $stream.Dispose()
    }
}

function Build-LineText($line) {
    # Windows 的中文 OCR 会在每个汉字之间插一个空格，同时真人打的空格也在。
    # 两者的区别只有间距：实测自动插入的间隙约为字宽的 5%，真人空格约 25%-33%。
    $words = @($line.Words)
    if ($words.Count -eq 0) { return "" }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append($words[0].Text)
    for ($i = 1; $i -lt $words.Count; $i++) {
        $previous = $words[$i - 1]
        $current = $words[$i]
        $previousEnd = $previous.BoundingRect.X + $previous.BoundingRect.Width
        $gap = $current.BoundingRect.X - $previousEnd
        $charWidth = $previous.BoundingRect.Width / [math]::Max($previous.Text.Length, 1)
        $ratio = if ($charWidth -gt 0.1) { $gap / $charWidth } else { 1.0 }
        if ($ratio -ge $SpaceRatioThreshold) {
            [void]$builder.Append(" ")
        }
        [void]$builder.Append($current.Text)
    }
    return $builder.ToString()
}

function Clean-Text([string]$text) {
    if ($null -eq $text) { return "" }
    $t = $text
    # 标点前不要空格
    $t = [regex]::Replace($t, '\s+(?=[\u3001\u3002\uff0c\uff01\uff1f\uff1b\uff1a\u201c\u201d\uff08\uff09])', '')
    return $t.Trim()
}

function Test-SystemLine([string]$text, [double]$centerRatio) {
    if ($text -match '^\d{1,2}:\d{2}(:\d{2})?$') { return $true }
    if ($text -match '^\d{4}\s*[\u5e74/-]\s*\d{1,2}\s*[\u6708/-]\s*\d{1,2}\s*\u65e5?') { return $true }
    if ($text -match '^(\u6628\u5929|\u4eca\u5929|\u524d\u5929|\u661f\u671f[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u65e5\u5929]|\u4e0a\u5348|\u4e0b\u5348|\u665a\u4e0a|\u51cc\u6668)') { return $true }
    if ($text -match '(\u64a4\u56de\u4e86\u4e00\u6761\u6d88\u606f|\u4ee5\u4e0a\u662f\u6253\u62db\u547c|\u62cd\u4e86\u62cd|\u52a0\u5165\u4e86\u7fa4\u804a|\u9080\u8bf7)') { return $true }
    # 微信里气泡永远靠左或靠右，居中的短行基本都是系统提示
    if ($centerRatio -gt 0.38 -and $centerRatio -lt 0.62 -and $text.Length -le 18) { return $true }
    return $false
}

function Get-LineGeometry($line) {
    $minX = [double]::MaxValue
    $maxX = 0.0
    $minY = [double]::MaxValue
    $maxY = 0.0
    $sum = 0.0
    $count = 0
    foreach ($word in $line.Words) {
        $rect = $word.BoundingRect
        if ($rect.X -lt $minX) { $minX = $rect.X }
        if (($rect.X + $rect.Width) -gt $maxX) { $maxX = $rect.X + $rect.Width }
        if ($rect.Y -lt $minY) { $minY = $rect.Y }
        if (($rect.Y + $rect.Height) -gt $maxY) { $maxY = $rect.Y + $rect.Height }
        $sum += ($rect.X + $rect.Width / 2)
        $count++
    }
    return [pscustomobject]@{
        MinX = $minX; MaxX = $maxX; MinY = $minY; MaxY = $maxY
        CenterRatio = if ($count -gt 0) { ($sum / $count) } else { 0 }
        Count = $count
    }
}

function Get-PreparedSlices([string]$filePath, [string]$tempDir) {
    $image = [System.Drawing.Image]::FromFile($filePath)
    try {
        $limit = $SliceHeight
        if ($limit -le 0) { $limit = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension }

        $cropTop = [int]([math]::Round($image.Height * $CropTopPercent))
        $cropBottom = [int]([math]::Round($image.Height * $CropBottomPercent))
        $cropHeight = $image.Height - $cropTop - $cropBottom
        if ($cropHeight -lt 1) { $cropHeight = $image.Height; $cropTop = 0 }

        $scale = $Scale
        if ($scale -le 0) { $scale = 1.0 }

        $needsWork = ($cropTop -gt 0) -or ($cropBottom -gt 0) -or ($scale -ne 1.0) -or ($cropHeight -gt $limit) -or $Invert
        if (-not $needsWork) { return @($filePath) }

        $outWidth = [int]([math]::Round($image.Width * $scale))
        if ($outWidth -lt 1) { $outWidth = 1 }
        # 每一片在原图里的最大高度：先用原图尺寸切片，再逐片放大。
        # 反过来做（先整体放大再切）会为超长截图创建上亿像素的位图，直接崩掉。
        $maxSourceSlice = [int]([math]::Floor($limit / $scale))
        if ($maxSourceSlice -lt 1) { $maxSourceSlice = $limit }
        if ($Invert -and $maxSourceSlice -gt $limit) { $maxSourceSlice = $limit }

        $paths = @()
        $index = 0
        $offset = 0
        while ($offset -lt $cropHeight) {
            $sliceHeight = [math]::Min($maxSourceSlice, $cropHeight - $offset)
            $outHeight = [int]([math]::Round($sliceHeight * $scale))
            if ($outHeight -lt 1) { $outHeight = 1 }
            # 先算好，别把加法写在参数列表里：PowerShell 会把逗号当成数组拼接，导致参数个数不对
            $sourceTop = $cropTop + $offset
            $slice = New-Object System.Drawing.Bitmap($outWidth, $outHeight)
            $graphics = [System.Drawing.Graphics]::FromImage($slice)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $dest = New-Object System.Drawing.Rectangle(0, 0, $outWidth, $outHeight)
            $src = New-Object System.Drawing.Rectangle(0, $sourceTop, $image.Width, $sliceHeight)
            if ($Invert) {
                # 深色模式截图：白字黑底反成黑字白底，OCR 准确率更高
                $matrix = New-Object System.Drawing.Imaging.ColorMatrix
                $matrix.Matrix00 = -1
                $matrix.Matrix11 = -1
                $matrix.Matrix22 = -1
                $matrix.Matrix33 = 1
                $matrix.Matrix44 = 1
                $matrix.Matrix40 = 1
                $matrix.Matrix41 = 1
                $matrix.Matrix42 = 1
                $attributes = New-Object System.Drawing.Imaging.ImageAttributes
                $attributes.SetColorMatrix($matrix)
                $graphics.DrawImage(
                    $image, $dest, $src.X, $src.Y, $src.Width, $src.Height,
                    [System.Drawing.GraphicsUnit]::Pixel, $attributes
                )
                $attributes.Dispose()
            } else {
                $graphics.DrawImage($image, $dest, $src, [System.Drawing.GraphicsUnit]::Pixel)
            }
            $graphics.Dispose()
            $slicePath = Join-Path $tempDir ("slice_{0:D3}.png" -f $index)
            $slice.Save($slicePath, [System.Drawing.Imaging.ImageFormat]::Png)
            $slice.Dispose()
            $paths += $slicePath
            $offset += $sliceHeight
            $index++
        }
        return $paths
    } finally {
        $image.Dispose()
    }
}

function Test-JunkLine([string]$text) {
    # 界面元素和识别乱码：数字/符号占比过高的行直接丢掉
    if ($text.Length -lt 1) { return $true }
    $junk = ([regex]::Matches($text, '[0-9@\u3008\u3009\u300a\u300b\uff0e\u00b7\u3001\uff08\uff09()\[\]{}<>~\^&%\$#*_+=\\|/]')).Count
    if (($junk / $text.Length) -gt 0.4) { return $true }
    return $false
}

$images = @()
# 从清单文件读路径（比命令行传数组更稳：中文名、空格、文件多都没问题）
if ([string]::IsNullOrWhiteSpace($ListFile) -eq $false -and (Test-Path $ListFile)) {
    $Files = @(
        Get-Content -LiteralPath $ListFile -Encoding UTF8 |
            Where-Object { $_.Trim() -ne "" } |
            ForEach-Object { $_.Trim() }
    )
}
if ($Files -and $Files.Count -gt 0) {
    # 只处理指定的图片（投喂时用这个，避免把老图重复识别）
    $images = @(
        $Files |
            Where-Object { $_ -and (Test-Path $_) } |
            ForEach-Object { Get-Item -LiteralPath $_ } |
            Where-Object { $_.Extension -match '^\.(png|jpg|jpeg|bmp|tif|tiff)$' } |
            Sort-Object Name
    )
} else {
    if ([string]::IsNullOrWhiteSpace($Folder) -or -not (Test-Path $Folder)) {
        Write-Host "找不到文件夹：$Folder" -ForegroundColor Red
        exit 1
    }
    $folderItem = Get-Item $Folder
    $images = @(
        Get-ChildItem -Path $folderItem.FullName -File |
            Where-Object { $_.Extension -match '^\.(png|jpg|jpeg|bmp|tif|tiff)$' } |
            Sort-Object Name
    )
}
if ($images.Count -eq 0) {
    Write-Host "这个文件夹里没有图片（支持 png / jpg / jpeg / bmp / tif）" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($OutFile)) {
    $OutFile = Join-Path (Split-Path $PSScriptRoot -Parent) "work\ocr_chat.txt"
}
$outFull = [System.IO.Path]::GetFullPath($OutFile)
$outDir = Split-Path $outFull -Parent
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

# 防手滑：同名文件已存在就先备份，绝不直接覆盖
if (Test-Path $outFull) {
    $backup = $outFull + ".bak"
    try {
        Copy-Item -LiteralPath $outFull -Destination $backup -Force
        Write-Host ("注意：已存在同名文件，旧内容已备份到 " + $backup) -ForegroundColor Yellow
    } catch {
        Write-Host ("无法备份已有文件，请先把 " + $outFull + " 改名再重试") -ForegroundColor Red
        exit 1
    }
}

$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("personabot_ocr_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

# 读取 OCR 修正表（把常见的误识别改回正确的字）
if ([string]::IsNullOrWhiteSpace($FixFile)) {
    $candidate = Join-Path $PSScriptRoot "ocr_fixes.txt"
    if (Test-Path $candidate) { $FixFile = $candidate }
}
$fixes = @()
if (-not [string]::IsNullOrWhiteSpace($FixFile) -and (Test-Path $FixFile)) {
    foreach ($line in (Get-Content -Encoding UTF8 $FixFile)) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -eq 2 -and $parts[0].Length -gt 0) {
            $fixes += [pscustomobject]@{ From = $parts[0]; To = $parts[1] }
        }
    }
    Write-Host ("已加载修正表：" + $FixFile + "（" + $fixes.Count + " 条规则）") -ForegroundColor Cyan
}

Write-Host "共 $($images.Count) 张图片，开始识别（用系统自带 OCR）……" -ForegroundColor Cyan

$messages = New-Object System.Collections.Generic.List[object]
$lineTotal = 0
$skipped = 0
$failedImages = 0
$script:fixCount = 0

foreach ($image in $images) {
    $lines = @()
    $slicePaths = @()
    try {
        $slicePaths = Get-PreparedSlices $image.FullName $tempDir
    } catch {
        Write-Host ("  跳过这张图（读取/切片失败）：" + $image.Name + " -> " + $_.Exception.Message) -ForegroundColor Yellow
        $failedImages++
        continue
    }
    foreach ($slicePath in $slicePaths) {
        try {
            $ocr = Invoke-OcrImage $slicePath
        } catch {
            Write-Host ("  识别失败：" + $image.Name + " -> " + $_.Exception.Message) -ForegroundColor Yellow
            continue
        }
        $imageWidth = [math]::Max($ocr.Width, 1)
        foreach ($line in $ocr.Result.Lines) {
            $geometry = Get-LineGeometry $line
            if ($geometry.Count -eq 0) { continue }
            $text = Clean-Text (Build-LineText $line)
            foreach ($fix in $fixes) {
                if ($text.Contains($fix.From)) {
                    $text = $text.Replace($fix.From, $fix.To)
                    $script:fixCount++
                }
            }
            if ($text -eq "") { continue }
            $lines += [pscustomobject]@{
                Text = $text
                CenterRatio = $geometry.CenterRatio / $imageWidth
                MaxRatio = $geometry.MaxX / $imageWidth
                MinX = $geometry.MinX
                MinY = $geometry.MinY
                MaxY = $geometry.MaxY
            }
        }
    }

    foreach ($line in $lines) {
        $lineTotal++
        # 用整张图的宽度做参照（切片时坐标是相对的，用最大值近似）
        $centerRatio = $line.CenterRatio
        if (Test-SystemLine $line.Text $centerRatio) { $skipped++; continue }
        if (Test-JunkLine $line.Text) { $skipped++; continue }

        if ($centerRatio -ge $SelfThreshold) {
            $speaker = $SelfName
        } elseif ($centerRatio -le $OtherThreshold) {
            $speaker = $OtherName
        } elseif ($line.MaxRatio -ge 0.85) {
            $speaker = $SelfName
        } else {
            $speaker = $OtherName
        }

        # 判断是不是上一句的续行（同一个人、紧接着、横向位置接近）
        $isContinuation = $false
        if ($messages.Count -gt 0) {
            $last = $messages[$messages.Count - 1]
            $gap = $line.MinY - $last.MaxY
            if ($last.Speaker -eq $speaker -and $gap -ge -20 -and $gap -lt 12) {
                $isContinuation = $true
            }
        }

        if ($isContinuation) {
            $last = $messages[$messages.Count - 1]
            if ($KeepLineBreaks) {
                $last.Text = $last.Text + "`n" + $line.Text
            } else {
                $last.Text = $last.Text + $line.Text
            }
            $last.MaxY = $line.MaxY
        } else {
            $messages.Add([pscustomobject]@{ Speaker = $speaker; Text = $line.Text; MaxY = $line.MaxY; MinX = $line.MinX })
        }
    }
    Write-Host ("  " + $image.Name)
}

$builder = New-Object System.Text.StringBuilder

# 乱码过滤：真实聊天用字重复率高，识别错字往往是全篇只出现一次的冷僻字。
# 一句话里冷僻字（全篇只出现 1 次）占比过高，就判断为乱码丢掉。
$charFreq = @{}
foreach ($message in $messages) {
    foreach ($ch in $message.Text.ToCharArray()) {
        $key = [string]$ch
        if ($charFreq.ContainsKey($key)) { $charFreq[$key]++ } else { $charFreq[$key] = 1 }
    }
}

$dropped = 0
foreach ($message in $messages) {
    if ($DropRareRatio -le 0) {
        [void]$builder.AppendLine($message.Speaker + ": " + ($message.Text -replace "\r?\n", " "))
        continue
    }
    $cjk = @($message.Text.ToCharArray() | Where-Object { [int][char]$_ -ge 0x4e00 -and [int][char]$_ -le 0x9fff })
    if ($cjk.Count -gt 0) {
        $rare = @($cjk | Where-Object { $charFreq[[string]$_] -le 1 }).Count
        if ($rare -ge 2 -and ($rare / $cjk.Count) -ge $DropRareRatio) {
            $dropped++
            continue
        }
    }
    [void]$builder.AppendLine($message.Speaker + ": " + ($message.Text -replace "\r?\n", " "))
}
[System.IO.File]::WriteAllText($outFull, $builder.ToString(), (New-Object System.Text.UTF8Encoding($false)))

Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue

$selfCount = @($messages | Where-Object { $_.Speaker -eq $SelfName }).Count
$otherCount = @($messages | Where-Object { $_.Speaker -eq $OtherName }).Count

Write-Host ""
Write-Host "完成" -ForegroundColor Green
Write-Host ("  识别文字行数：" + $lineTotal + "（跳过系统提示 " + $skipped + " 行）")
Write-Host ("  整理出消息：" + $messages.Count + " 条")
if ($failedImages -gt 0) {
    Write-Host ("  有 " + $failedImages + " 张图没能处理（已跳过，不影响其它图片）") -ForegroundColor Yellow
}
if ($dropped -gt 0) {
    Write-Host ("  丢弃疑似乱码：" + $dropped + " 条，最终保留 " + ($messages.Count - $dropped) + " 条")
}
if ($script:fixCount -gt 0) {
    Write-Host ("  按修正表改正错字：" + $script:fixCount + " 处")
}
Write-Host ("  " + $SelfName + "：" + $selfCount + " 条　" + $OtherName + "：" + $otherCount + " 条")
Write-Host ("  输出文件：" + $outFull)
Write-Host ""
Write-Host "接下来用这个文件生成人格：" -ForegroundColor Cyan
Write-Host ("  python run.py profile --chat `"" + $outFull + "`" --me " + $SelfName)
Write-Host ""
Write-Host "如果左右判断反了（把对方认成了自己），加上 -SelfThreshold 0.5 再跑一次；"
Write-Host "如果长截图被切断，可以试试 -SliceHeight 6000。"
Write-Host ""
