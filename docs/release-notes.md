簡譜工房 0.1.1 — 安裝與中文路徑修正版

修復安裝到 Program Files 時的權限流程，提供個人／所有使用者兩種安裝模式。修復照片辨識在中文安裝路徑無法載入 Java 的問題。建議 0.1.0 使用者更新；Windows 10 需 1903 或更新版本。


下載 **JianpuStudio-0.1.1-Windows-x64-Setup.exe**，按安裝精靈完成即可。內附 Python、Qt、LilyPond、Audiveris、Java、英文 OCR 模型與繁體中文字型；不用另外裝環境。也提供免安裝 ZIP。

- 繁體中文音符編輯、拍數檢查、復原重做、專案存檔與意外中斷復原。
- LilyPond PDF 預覽／匯出、MIDI 匯出與 Windows 內建試聽。
- 五線譜照片整理與 Audiveris 辨識，再轉為可校對的簡譜；MusicXML 匯入。
- 進階 jianpu-ly 原始碼模式。

適用 Windows 10/11 x64，建議 8 GB RAM、2 GB 可用空間。照片辨識需要校對，不支援手寫或數字簡譜照片，中文歌詞需手動輸入。這是初始版本，安裝檔尚未做 Authenticode 簽章。請核對 SHA256SUMS.txt。

來源、授權、限制與完整操作方式請見 repo README 及 docs。Windows 自動驗證涵蓋測試、凍結後執行檔、實際排譜／照片辨識，以及安裝與移除。
