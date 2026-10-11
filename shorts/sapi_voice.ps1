param(
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$AudioFile,
    [Parameter(Mandatory = $true)][string]$TimingFile,
    [string]$VoiceName = 'Microsoft Zira Desktop'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $installed = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
    if ($installed -contains $VoiceName) {
        $synth.SelectVoice($VoiceName)
    }
    $events = New-Object 'System.Collections.Generic.List[object]'
    $handler = [System.EventHandler[System.Speech.Synthesis.SpeakProgressEventArgs]]{
        param($sender, $event)
        $events.Add([pscustomobject]@{
            text = $event.Text
            start = $event.AudioPosition.TotalSeconds
        })
    }
    $synth.add_SpeakProgress($handler)
    $synth.SetOutputToWaveFile($AudioFile)
    $synth.Speak($text)
    $json = $events | ConvertTo-Json -Depth 3 -Compress
    [System.IO.File]::WriteAllText($TimingFile, $json, [System.Text.Encoding]::UTF8)
}
finally {
    $synth.Dispose()
}
