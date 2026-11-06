# Rebuild the README diagrams: TikZ -> PDF -> SVG (+ PNG preview).
# Needs a TeX distribution with pdflatex and pdftocairo (MiKTeX or TeX Live).
param([string[]]$Names = @('architecture', 'killchain', 'sequence', 'bench'))
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$out = Join-Path $here '..\img'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$tmp = Join-Path ([IO.Path]::GetTempPath()) 'saavdhaan-diagrams'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Push-Location $here
try {
  foreach ($n in $Names) {
    pdflatex -interaction=nonstopmode -halt-on-error -output-directory $tmp "$n.tex" | Out-Null
    if ($LASTEXITCODE) { Get-Content (Join-Path $tmp "$n.log") -Tail 30; throw "pdflatex failed on $n" }
    pdftocairo -svg (Join-Path $tmp "$n.pdf") (Join-Path $out "$n.svg")
    pdftocairo -png -r 110 -singlefile (Join-Path $tmp "$n.pdf") (Join-Path $tmp $n)
    "built $n"
  }
} finally { Pop-Location }
