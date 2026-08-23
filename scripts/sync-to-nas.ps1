# Push O:\tides-pool -> NAS. Prefer editing on NAS; this is a fallback.
$ErrorActionPreference = "Stop"
$src = "O:\tides-pool"
$remote = "bip110-nas"
$dst = "/mnt/Alexandria/local/tides-pool"
$tmp = Join-Path $env:TEMP "tides-pool-sync.tgz"

Write-Host "Packing $src -> ${remote}:$dst"
if (Test-Path $tmp) { Remove-Item $tmp -Force }
tar -C $src `
  --exclude=.venv `
  --exclude=.git `
  --exclude=__pycache__ `
  --exclude=.pytest_cache `
  --exclude=src/tides_pool.egg-info `
  --exclude=deploy/postgres-data `
  -czf $tmp .
scp $tmp "${remote}:/tmp/tides-pool-sync.tgz"
ssh $remote "cd $dst; tar -xzf /tmp/tides-pool-sync.tgz; rm -f /tmp/tides-pool-sync.tgz; find . -type f | wc -l"
Remove-Item $tmp -Force
Write-Host "OK - primary tree is $dst on $remote"
