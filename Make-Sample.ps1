# Uses an installed Windows voice. Does not play or capture sound.
. "$PSScriptRoot\Environment.ps1"
New-Item -ItemType Directory -Force -Path "$PSScriptRoot\test-results" | Out-Null
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $english = $voice.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'en-*' } | Select-Object -First 1
    if (-not $english) { throw 'No installed English Windows voice was found.' }
    $voice.SelectVoice($english.VoiceInfo.Name)
    $voice.Rate = -1
    $voice.SetOutputToWaveFile("$PSScriptRoot\test-results\english-sample.wav")
    $voice.Speak('Hello everyone. Today we will learn how to build a useful application. Please open the settings and check your audio device.')
} finally {
    $voice.Dispose()
}
Write-Host 'Created test-results\english-sample.wav without playing or recording sound.'
