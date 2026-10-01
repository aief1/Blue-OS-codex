Add-Type -AssemblyName System.Drawing

$size = 512
$outputDirectory = Join-Path $PSScriptRoot '..\src\assets\images'
$previewDirectory = Join-Path $PSScriptRoot '..\design-reference'
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $previewDirectory -Force | Out-Null

function New-RoundedPath {
    param(
        [float] $X,
        [float] $Y,
        [float] $Width,
        [float] $Height,
        [float] $Radius
    )

    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $diameter = $Radius * 2
    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

$canvas = [System.Drawing.Bitmap]::new($size, $size)
$graphics = [System.Drawing.Graphics]::FromImage($canvas)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$graphics.Clear([System.Drawing.Color]::Transparent)

try {
    $background = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        [System.Drawing.Rectangle]::new(12, 12, 488, 488),
        [System.Drawing.ColorTranslator]::FromHtml('#202329'),
        [System.Drawing.ColorTranslator]::FromHtml('#101216'),
        90.0
    )
    try { $graphics.FillEllipse($background, 12, 12, 488, 488) }
    finally { $background.Dispose() }

    # A five-column activity mark: clear at launcher size and consistent with the watch UI.
    $barWidth = 48
    $gap = 21
    $heights = @(112, 180, 262, 180, 112)
    $colors = @('#69B774', '#8DE898', '#CBFFAA', '#8DE898', '#69B774')
    $totalWidth = $heights.Count * $barWidth + ($heights.Count - 1) * $gap
    $left = ($size - $totalWidth) / 2

    for ($i = 0; $i -lt $heights.Count; $i++) {
        $height = $heights[$i]
        $x = [float]($left + $i * ($barWidth + $gap))
        $y = [float](($size - $height) / 2)
        $path = New-RoundedPath -X $x -Y $y -Width $barWidth -Height $height -Radius 17
        $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($colors[$i]))
        try { $graphics.FillPath($brush, $path) }
        finally { $brush.Dispose(); $path.Dispose() }
    }

    $preview = Join-Path $previewDirectory 'codex-pulse-preview.png'
    $canvas.Save($preview, [System.Drawing.Imaging.ImageFormat]::Png)

    $icon = [System.Drawing.Bitmap]::new(114, 114)
    $iconGraphics = [System.Drawing.Graphics]::FromImage($icon)
    try {
        $iconGraphics.Clear([System.Drawing.Color]::Transparent)
        $iconGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $iconGraphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $iconGraphics.DrawImage($canvas, [System.Drawing.Rectangle]::new(0, 0, 114, 114))
        $icon.Save((Join-Path $outputDirectory 'codex-pulse.png'), [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally { $iconGraphics.Dispose(); $icon.Dispose() }
}
finally { $graphics.Dispose(); $canvas.Dispose() }
