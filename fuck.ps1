while ($true) {
    & "C:\Users\Tadhg Murray\.fly\bin\flyctl.exe" proxy 5433:5432 -a xc-predictor-db

    "$(Get-Date): fly proxy exited, restarting in 5s" | Out-File -Append fly_proxy_restarts.log
    Start-Sleep -Seconds 5
}