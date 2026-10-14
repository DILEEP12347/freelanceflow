# Week 7 demo helper.   Usage:  powershell -ExecutionPolicy Bypass -File week7.ps1 <action>
# Actions: project | time | expense | bill | pay | reports | audit | platform
param([string]$action = "help")

$acme  = "http://acme.localhost:8000/api"     # an organization's own address
$pub   = "http://localhost:8000/api"           # the bare domain (platform admin)
$state = Join-Path $env:TEMP "ff-week7-state.json"
$tmp   = Join-Path $env:TEMP "ff-week7-body.json"
$utc   = (Get-Date).ToUniversalTime()
$today = $utc.ToString("yyyy-MM-dd")

function Login($baseUrl, $file) {
  $r = curl.exe -s -X POST -H "Content-Type: application/json" -d "@$file" "$baseUrl/auth/login/"
  $t = ($r | ConvertFrom-Json).access
  if (-not $t) { Write-Host "Login failed ($file): $r"; exit 1 }
  return $t
}

function Api($token, $baseUrl, $method, $path, $body) {
  $auth = "Authorization: Bearer $token"
  if ($body) {
    [System.IO.File]::WriteAllText($tmp, $body)
    $out = curl.exe -s -X $method -H $auth -H "Content-Type: application/json" -d "@$tmp" "$baseUrl/$path"
  } else {
    $out = curl.exe -s -X $method -H $auth "$baseUrl/$path"
  }
  if (-not $out) { return $null }
  return ($out | ConvertFrom-Json)
}

function Money($minor) { return ("INR {0:N2}" -f ([double]$minor / 100)) }
function Iso($dt) { return $dt.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'") }
function Load-State {
  if (-not (Test-Path $state)) { Write-Host "Run the 'project' action first."; exit 1 }
  return (Get-Content $state -Raw | ConvertFrom-Json)
}
function Save-State($obj) { $obj | ConvertTo-Json | Set-Content $state }
function NeedsBusiness($r) {
  if ($r -and $r.code -eq "feature_not_available") {
    Write-Host "This needs the Business plan. Run the plan command from step 1 first."; exit 1
  }
}

switch ($action) {

  "project" {
    $t = Login $acme "login.json"
    $client = Api $t $acme "POST" "clients/" '{"name":"Studio Client","email":"billing@studioclient.example"}'
    if (-not $client.id) { Write-Host "Could not create the client:"; $client | ConvertTo-Json; exit 1 }
    $project = Api $t $acme "POST" "projects/" ('{"name":"Website build","client":' + $client.id + ',"hourly_rate_minor":150000}')
    if (-not $project.id) { Write-Host "Could not create the project:"; $project | ConvertTo-Json; exit 1 }
    Save-State @{ client = $client.id; project = $project.id }
    Write-Host "Client '$($client.name)' and project '$($project.name)' created at $(Money $project.hourly_rate_minor) per hour."
  }

  "time" {
    $st = Load-State
    $t = Login $acme "login.json"
    $a = Api $t $acme "POST" "time/" ('{"project":' + $st.project + ',"description":"Homepage design","started_at":"' + (Iso $utc.AddHours(-5)) + '","duration_minutes":60}')
    $b = Api $t $acme "POST" "time/" ('{"project":' + $st.project + ',"description":"API integration","started_at":"' + (Iso $utc.AddHours(-3)) + '","duration_minutes":30}')
    if (-not $a.id -or -not $b.id) { Write-Host "Could not add time:"; $a | ConvertTo-Json; $b | ConvertTo-Json; exit 1 }
    Write-Host "Logged by hand: $($a.hours) h '$($a.description)' = $(Money $a.amount_minor), and $($b.hours) h '$($b.description)' = $(Money $b.amount_minor)"
    $run = Api $t $acme "POST" "time/start/" ('{"project":' + $st.project + ',"description":"Live timer demo"}')
    Write-Host "Timer started (entry $($run.id)), running = $($run.is_running)"
    $cur = Api $t $acme "GET" "time/running/" $null
    Write-Host "Running right now: '$($cur.running.description)'"
    Start-Sleep -Seconds 3
    $stop = Api $t $acme "POST" "time/$($run.id)/stop/" $null
    Write-Host "Stopped after $($stop.duration_seconds) seconds. Running now: $($stop.is_running)"
    $list = Api $t $acme "GET" "time/?client=$($st.client)" $null
    $s = $list.summary
    Write-Host ("Totals for this client: {0:N2} h logged, {1:N2} h billable, unbilled value {2}" -f ($s.total_seconds / 3600), ($s.billable_seconds / 3600), (Money $s.unbilled_value_minor))
  }

  "expense" {
    $st = Load-State
    $t = Login $acme "login.json"
    $d = Api $t $acme "POST" "expenses/" ('{"description":"Domain renewal","vendor":"Namecheap","category":"Software","amount_minor":118000,"tax_minor":18000,"incurred_on":"' + $today + '","client":' + $st.client + ',"billable":true}')
    $o = Api $t $acme "POST" "expenses/" ('{"description":"Coworking day pass","category":"Office","amount_minor":50000,"tax_minor":0,"incurred_on":"' + $today + '"}')
    if (-not $d.id -or -not $o.id) { Write-Host "Could not add the expenses:"; $d | ConvertTo-Json; $o | ConvertTo-Json; exit 1 }
    Write-Host "Billable to the client: $($d.description), $(Money $d.amount_minor) (of which GST $(Money $d.tax_minor))"
    Write-Host "Our own cost:           $($o.description), $(Money $o.amount_minor)"
  }

  "bill" {
    $st = Load-State
    $t = Login $acme "login.json"
    $tl = Api $t $acme "GET" "time/?client=$($st.client)&billed=false" $null
    $tIds = @($tl.results | Where-Object { -not $_.is_running } | ForEach-Object { $_.id })
    $ex = Api $t $acme "GET" "expenses/?client=$($st.client)&billed=false&billable=true" $null
    $eIds = @($ex.results | ForEach-Object { $_.id })
    $rates = Api $t $acme "GET" "tax-rates/" $null
    $gst = ($rates | Where-Object { $_.rate_bps -eq 1800 } | Select-Object -First 1).id
    $body = '{"client":' + $st.client + ',"time_entry_ids":[' + ($tIds -join ",") + '],"expense_ids":[' + ($eIds -join ",") + '],"tax_rate_id":' + $gst + '}'
    $r = Api $t $acme "POST" "work/invoice/" $body
    if (-not $r.invoice_id) { Write-Host "Could not create the invoice:"; $r | ConvertTo-Json -Depth 4; exit 1 }
    Write-Host "Draft invoice created from $($r.time_entries) time entries and $($r.expenses) expense(s). Total $(Money $r.total_minor)"
    $inv = Api $t $acme "GET" "invoices/$($r.invoice_id)/" $null
    foreach ($l in $inv.lines) { Write-Host ("  {0,-45} qty {1,6}  rate {2,14}" -f $l.description, $l.quantity, (Money $l.unit_price_minor)) }
    $sent = Api $t $acme "POST" "invoices/$($r.invoice_id)/send/" '{}'
    Write-Host "Sent as $($sent.number), due $($sent.due_date)."
    $again = Api $t $acme "GET" "time/?client=$($st.client)&billed=false" $null
    Write-Host "Time entries still unbilled: $($again.count)   (they are now locked to the invoice)"
    Save-State @{ client = $st.client; project = $st.project; invoice = $r.invoice_id }
  }

  "pay" {
    $st = Load-State
    $t = Login $acme "login.json"
    $inv = Api $t $acme "GET" "invoices/$($st.invoice)/" $null
    $half = [math]::Floor($inv.total_minor / 2)
    $p = Api $t $acme "POST" "invoices/$($st.invoice)/payments/" ('{"amount_minor":' + $half + ',"method":"upi","reference":"DEMO-UPI-1"}')
    if (-not $p.id) { Write-Host "Could not record the payment:"; $p | ConvertTo-Json; exit 1 }
    $after = Api $t $acme "GET" "invoices/$($st.invoice)/" $null
    Write-Host "Recorded $(Money $half). Status: $($after.status). Balance due: $(Money $after.balance_due_minor)"
  }

  "reports" {
    $t = Login $acme "login.json"
    $rev = Api $t $acme "GET" "reports/revenue/" $null
    NeedsBusiness $rev
    foreach ($c in $rev.currencies) {
      Write-Host "REVENUE ($($c.currency)): invoiced $(Money $c.totals.invoiced_minor), collected $(Money $c.totals.collected_minor)"
      foreach ($m in $c.months) {
        if ($m.invoiced_minor -ne 0 -or $m.collected_minor -ne 0) { Write-Host ("  {0}   invoiced {1,16}   collected {2,16}" -f $m.month, (Money $m.invoiced_minor), (Money $m.collected_minor)) }
      }
    }
    $rec = Api $t $acme "GET" "reports/receivables/" $null
    foreach ($c in $rec.currencies) {
      Write-Host "`nWHO OWES US ($($c.currency)) as of $($rec.as_of): total $(Money $c.total_minor)"
      foreach ($p in $c.buckets.PSObject.Properties) { Write-Host ("  {0,-9} {1,16}" -f $p.Name, (Money $p.Value)) }
    }
    $pl = Api $t $acme "GET" "reports/profit-loss/" $null
    foreach ($c in $pl.currencies) {
      $x = $c.totals
      Write-Host "`nPROFIT AND LOSS ($($c.currency), $($pl.basis)):"
      Write-Host "  income   $(Money $x.income_minor)"
      Write-Host "  expenses $(Money $x.expenses_minor)"
      Write-Host "  profit   $(Money $x.profit_minor)"
      Write-Host "  GST collected $(Money $x.gst_collected_minor), GST paid $(Money $x.gst_paid_minor), net owed $(Money $x.net_gst_payable_minor)"
    }
    $cl = Api $t $acme "GET" "reports/clients/?limit=5" $null
    foreach ($c in $cl.currencies) {
      Write-Host "`nTOP CLIENTS ($($c.currency)):"
      foreach ($r in $c.clients) { Write-Host ("  {0,-22} invoiced {1,14}   outstanding {2,14}" -f $r.client_name, (Money $r.invoiced_minor), (Money $r.outstanding_minor)) }
    }
    $tm = Api $t $acme "GET" "reports/time/" $null
    Write-Host "`nTIME BY PROJECT:"
    foreach ($p in $tm.projects) { Write-Host ("  {0,-22} {1,6:N2} h total, {2,6:N2} h unbilled, unbilled value {3}" -f $p.project, ($p.total_seconds / 3600), ($p.unbilled_seconds / 3600), (Money $p.unbilled_value_minor)) }
  }

  "audit" {
    $t = Login $acme "login.json"
    $a = Api $t $acme "GET" "audit/" $null
    NeedsBusiness $a
    Write-Host "Newest first ($($a.count) entries in total):"
    $a.results | Select-Object -First 18 | ForEach-Object {
      Write-Host ("{0}  {1,-8} {2,-26} {3,-28} {4}" -f $_.created_at, $_.outcome, $_.action, $_.actor_email, $_.object_repr)
    }
  }

  "platform" {
    $t = Login $pub "root.json"
    $ov = Api $t $pub "GET" "platform/overview/" $null
    if (-not $ov.organizations) { Write-Host "Not allowed or failed:"; $ov | ConvertTo-Json; exit 1 }
    Write-Host "ORGANIZATIONS: $($ov.organizations) ($($ov.new_organizations_30d) new in the last 30 days)"
    foreach ($p in $ov.plans) { Write-Host ("  {0,-9} {1} organization(s)" -f $p.name, $p.organizations) }
    Write-Host "PAYING: $($ov.subscriptions.paying)   TRIALING: $($ov.subscriptions.trialing)   PAST DUE: $($ov.subscriptions.past_due)"
    foreach ($m in $ov.mrr) { Write-Host "MRR $(Money $m.amount_minor)" }
    foreach ($m in $ov.arr) { Write-Host "ARR $(Money $m.amount_minor)" }
    Write-Host "CHURN (30 days): $($ov.churn_30d.cancellations) cancellation(s), rate $($ov.churn_30d.rate)"
    $orgs = Api $t $pub "GET" "platform/organizations/" $null
    Write-Host "`nALL ORGANIZATIONS:"
    foreach ($o in $orgs.results) { Write-Host ("  {0,-16} {1,-22} plan {2,-9} (now {3,-9}) {4,-9} {5} member(s)  owner {6}" -f $o.name, $o.domain, $o.plan, $o.effective_plan, $o.status, $o.members, $o.owner_email) }
  }

  default {
    Write-Host "Use: week7.ps1 project | time | expense | bill | pay | reports | audit | platform"
  }
}
