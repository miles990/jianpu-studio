# 簡譜工房 · Jianpu Studio

**在 Windows 上用滑鼠寫簡譜、預覽排版，將五線譜照片轉成可校對的簡譜。**

繁體中文桌面程式，整合 [GNU LilyPond](https://github.com/lilypond/lilypond)、[jianpu-ly](https://github.com/ssb22/jianpu-ly) 與 [Audiveris](https://github.com/Audiveris/audiveris)。不需要學命令列。

![簡譜工房實際介面](docs/screenshots/editor.png)

## 下載與安裝

前往 **[Releases 下載](https://github.com/miles990/jianpu-studio/releases/latest)**，選擇 `JianpuStudio-0.1.0-Windows-x64-Setup.exe`。

1. 雙擊安裝檔，依精靈按「下一步 → 安裝」。
2. 從桌面開啟「簡譜工房」。
3. 先打開內建「小星星」，試著修改音符，再匯出 PDF。

**一個安裝檔包含所有必要環境**：Python、Qt、LilyPond、Audiveris、Java、英文 OCR 字庫與繁體中文字型。不用設定 PATH、不用逐一安裝工具，安裝後編輯、排譜、辨識皆可離線使用。安裝在目前使用者資料夾，不要求管理員權限。

支援 Windows 10/11 **x64**。建議 8 GB RAM、2 GB 可用空間；複雜照片辨識建議 16 GB RAM。也提供 `Portable.zip`：解壓縮整個資料夾後開啟 `JianpuStudio.exe`，不要只搬走 exe。首次公開版尚未購置程式碼簽章，Windows 可能顯示未知發行者；請核對本 repo 的 Release 來源及 `SHA256SUMS.txt`。不要關閉防毒保護。

## 可以做什麼

- **點選編輯**：0–7 音符／休止符、升降記號、上下兩個八度、全音符到三十二分音符、附點、插入／複製／刪除／移動。
- **基本樂曲設定**：曲名、作曲者、調號、拍號、速度、中文字歌詞、五線譜對照。
- **拍數檢查**：指出跨小節／未滿小節，提供一鍵補休止符。
- **真正的樂譜排版**：LilyPond PDF，多頁預覽、縮放、透過系統 PDF 閱讀器列印。
- **匯出與試聽**：PDF、MIDI、`.jly` 簡譜文字、`.ly`；Windows 內建 MIDI 合成器試聽／停止。
- **保存工作**：`.jps` 專案、復原／重做、原子寫入、每 15 秒本機復原暫存。
- **進階簡譜**：直接編輯 jianpu-ly 語法，處理多聲部、反覆、連線等記號。
- **照片轉簡譜**：裁白邊／旋轉／對比 → Audiveris → MusicXML → 簡譜，旁邊保留原圖供校對。
- **MusicXML 匯入**：`.musicxml`、`.xml`、`.mxl`；簡單單聲部轉為視覺音符，複雜樂譜保留在進階模式。

## 照片辨識的適用範圍

目前辨識的是**清晰的印刷五線譜照片或掃描圖**，不是數字簡譜影像，也不支援手寫譜。陰影、歪斜、模糊、裁到譜線、複雜多聲部等會降低準確率。每次辨識完成都需要核對調號、音高、拍數、連結線、反覆與歌詞。

Audiveris／jianpu-ly 的 MusicXML 匯入仍有各自限制；不能保證任意照片能完整還原。照片與樂譜不會上傳。英文 OCR 字庫供標題及文字輔助，**中文歌詞 OCR 不在此版本支援範圍內**。

視覺編輯目前是單聲部，不提供直接在正式 PDF 上拖曳音符。多聲部、跨小節延音、弱起、三連音等請使用進階簡譜。照片原圖與中間辨識檔只在本次工作階段保留；需要保留時請儲存原圖並匯出 MusicXML，`.jps` 會保留可再編輯的簡譜內容。

詳見 [使用手冊](docs/user-guide.md)、[架構與測試](docs/development.md)、[第三方授權](THIRD_PARTY_NOTICES.md)。

## 開發執行

```powershell
git clone https://github.com/miles990/jianpu-studio.git
cd jianpu-studio
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts/fetch_runtime.py --platform windows
$env:JIANPU_LILYPOND = "$pwd/vendor-runtime/lilypond/bin/lilypond.exe"
$env:JIANPU_AUDIVERIS = "$pwd/vendor-runtime/audiveris/Audiveris.exe"
$env:TESSDATA_PREFIX = "$pwd/vendor-runtime/tessdata"
python -m jianpu_studio
```

使用者應下載安裝檔；以上步驟僅供開發者。CI 在 Linux 與 Windows 跑測試，並用打包後的 exe 實測 PDF／MIDI、照片辨識、安裝與移除。

## 授權

本程式 **MIT**。jianpu-ly 保留 Apache-2.0 原始授權與作者資訊；LilyPond 為獨立 GPL 工具，Audiveris 為獨立 AGPL 工具，Qt/PySide6 為動態連結 LGPL 元件。各工具、字型、模型及來源連結見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

This is an independent project, not an official LilyPond, jianpu-ly or Audiveris product.
