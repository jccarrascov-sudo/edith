# Northwind Desktop: a small invoice-entry app for the demo of recipe levels 4 and 5.
# It has only a user interface: no file format to write, no COM, no web API. Saved invoices go
# to its own private store (one JSON object per line), by default in %LOCALAPPDATA%.
#   powershell -NoProfile -ExecutionPolicy Bypass -File northwind.ps1 [-Store path] [-Version v2]
# v2 (for the repair benchmark): "Amount" is renamed "Total due (PEN)" and moved last.
param([string]$Store = "$env:LOCALAPPDATA\NorthwindDesktop\invoices.jsonl", [string]$Version = "v1")

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = "Northwind Desktop - New invoice"
$form.ClientSize = New-Object System.Drawing.Size(420, 300)
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.StartPosition = "CenterScreen"
$form.Font = New-Object System.Drawing.Font("Segoe UI", 10)

$fields = @("Client", "Tax ID", "Date", "Amount", "Description")
if ($Version -eq "v2") { $fields = @("Client", "Tax ID", "Date", "Description", "Total due (PEN)") }
$boxes = @{}
$y = 20
foreach ($name in $fields) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $name
    $label.Location = New-Object System.Drawing.Point(20, ($y + 3))
    $label.Size = New-Object System.Drawing.Size(100, 24)
    $form.Controls.Add($label)
    $box = New-Object System.Windows.Forms.TextBox
    $box.Location = New-Object System.Drawing.Point(130, $y)
    $box.Size = New-Object System.Drawing.Size(260, 26)
    $box.AccessibleName = $name
    $box.Name = ($name -replace " ", "")
    $form.Controls.Add($box)
    $boxes[$name] = $box
    $y += 40
}

$status = New-Object System.Windows.Forms.Label
$status.Location = New-Object System.Drawing.Point(20, 262)
$status.Size = New-Object System.Drawing.Size(250, 24)
$status.AccessibleName = "Status"
$form.Controls.Add($status)

$save = New-Object System.Windows.Forms.Button
$save.Text = "Save"
$save.Location = New-Object System.Drawing.Point(290, 255)
$save.Size = New-Object System.Drawing.Size(100, 32)
$save.Add_Click({
    $record = [ordered]@{}
    foreach ($name in $fields) { $record[($name -replace "^Total due \(PEN\)$", "Amount")] = $boxes[$name].Text }
    $dir = Split-Path -Parent $Store
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $line = ($record | ConvertTo-Json -Compress)
    [System.IO.File]::AppendAllText($Store, $line + "`n", (New-Object System.Text.UTF8Encoding($false)))
    foreach ($name in $fields) { $boxes[$name].Text = "" }
    $status.Text = "Saved invoice for " + $record["Client"]
})
$form.Controls.Add($save)
$form.AcceptButton = $save

[void]$form.ShowDialog()
