ParAILegal {version} for Windows 10 and 11 (64-bit): offline Indian legal research. Built and published by the release workflow (.github/workflows/release.yml).

## Install

1. Download `ParAILegal_{version}_x64-setup.exe` ({size}) below and run it. It installs for your user only; no administrator rights are needed.
2. Open ParAILegal and go to **Downloads** to fetch, once:
   - the answer model (Qwen3.5 4B, 2.7 GB): written answers
   - the Supreme Court judgments (1.0 GB): judgments search, the citator, precedents
   - the Case Builder model (Qwen3.5 9B, 5.6 GB), optional: unlocks the Case Builder

   They come from Hugging Face ([models](https://huggingface.co/Sarthak213/parailegal-models), [judgments](https://huggingface.co/datasets/Sarthak213/parailegal-sc-judgments)). Downloads resume if they stop, and each file is checked against its SHA-256 before use. After that the app works offline.

The app installs to `%LOCALAPPDATA%\ParAILegal`; its downloads go to `%LOCALAPPDATA%\in.parailegal.desktop`. To remove everything, uninstall from Windows' Apps settings and tick **Delete the application data**.

## Not code-signed

The installer is **not code-signed**. Windows SmartScreen will warn that it comes from an unknown publisher; choose **More info**, then **Run anyway**.

Check your copy first (PowerShell: `Get-FileHash .\ParAILegal_{version}_x64-setup.exe -Algorithm SHA256`). It should be:

```
{sha256}
```

If it differs, do not run the file.

ParAILegal is a research tool, not legal advice. The app is licensed under the AGPL-3.0; the components it ships or downloads keep their own licences (`THIRD_PARTY_NOTICES.txt` in the install folder).
