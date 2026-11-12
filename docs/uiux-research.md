# 簡譜工房 UI／UX 研究與改版依據

研究日期：2026-11-12。目標使用者：用過 EOP、主要製作二胡簡譜的一般 Windows 使用者。

本次研究閱讀官方產品說明、操作手冊及維護者文件，涵蓋主要代表性產品。不是所有產品的實機比較，也不宣稱窮盡市場或證明某一設計對所有人最好。以下「採用」是本專案依使用情境做的設計判斷；實作狀態另列，避免把計畫當成已發布功能。

## 競品與可採用的做法

| 類別／產品 | 查核來源與觀察 | 本專案取捨 |
| --- | --- | --- |
| EOP 簡譜大師 | [官方介紹](https://www.everyonepiano.cn/html/jianpu.html)、[操作示範與官方回覆](https://www.everyonepiano.cn/Video-2584.html)、[譜面布局教學](https://www.everyonepiano.cn/Video-4588-%E5%A6%82%E4%BD%95%E8%B0%83%E6%95%B4%E8%B0%B1%E9%9D%A2%E5%B8%83%E5%B1%80-EOP%E7%AE%80%E8%B0%B1%E5%A4%A7%E5%B8%88%E4%BD%BF%E7%94%A8%E6%95%99%E7%A8%8B.html)。數字輸入、輸入／修改模式；官方明確說明空白鍵切換模式。 | 以熟悉操作為基準。常用工具靠近譜面；空白切換新增／修改，試聽使用獨立按鈕。相容的是明確列出的操作，不代表 EOPN 格式或所有快捷鍵相容。 |
| JP-Word | [官方介紹](https://www.happyeo.com/intro_jpw.htm)、[作者的使用手冊](https://happyeo.gitbooks.io/jp-word-manual/content/01/00.html)。強調如打字般輸入及所見即所得。官方介紹頁直接抓取曾逾時，另以官方手冊核對。 | 減少輸入時反覆切換工具。顯示下一個音符會插在哪裡；讓曲名、調號、拍號與音符操作有固定位置。 |
| MuseScore Studio | [選取元素](https://handbook.musescore.org/en_gb/basics/selecting-elements)、[介面結構](https://handbook.musescore.org/en_gb/navigation/the-user-interface)、[輸入音符](https://handbook.musescore.org/en_gb/basics/entering-notes-and-rests)。區分單選、多選與範圍選取，選取狀態清楚。 | Shift 範圍選取、Ctrl 個別加選、整段複製／刪除；選取幾個音符、修改哪個屬性要明確可見。 |
| Dorico | [插入模式](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/write_mode/write_mode_insert_mode/write_mode_insert_mode_c.html)、[閱讀視圖切換](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/user_interface/user_interface_view_type_changing_t.html)。插入行為會影響後續音樂位置，編寫與頁面呈現有不同目的。 | 明確選擇插前、插後或曲尾；編輯畫布與正式 PDF 各自清楚標示，避免將非正式畫布當成列印成果。 |
| Sibelius | [Panorama 官方說明](https://secure.sibelius.com/products/sibelius/5/panorama.html)。連續音樂視圖用於減少創作時頁面干擾；這是設計概念來源，不當作現版完整功能評測。 | 編輯時以樂句與小節閱讀，列印時交由 LilyPond 做正式頁面排版。 |
| Flat | [複製／剪下／貼上](https://help.flat.io/en/music-notation-software/paste/)、[工具列重整](https://flat.io/changelog/2026-03-26-a-new-editor-toolbar-on-the-web)。選取與插入位置清楚，符號與版面設定分組。 | 讓複製與重複分成不同動作，使用系統剪貼簿；常用音符工具固定，較少用的紙張設定另開面板。 |
| Noteflight | [編輯器功能](https://support.noteflight.com/hc/en-us/articles/360020883831-Editor-Features)、[快捷鍵](https://support.noteflight.com/hc/en-us/articles/360020463271-Keyboard-Shortcuts)。提供可查找的操作說明。 | 按鈕提示與一頁快捷鍵說明，讓新手能用滑鼠，熟手能逐步學鍵盤。 |
| Frescobaldi | [官方功能](https://www.frescobaldi.org/)。LilyPond 文字編輯、Music View、Point & Click、MIDI 與節奏處理。 | 保留進階語法入口與真正 LilyPond 排版；視覺編輯維持優先。正式 PDF 直接點擊回原始碼不是目前已完成能力。 |
| Soundslice | [掃描譜校對](https://www.soundslice.com/help/en/creating/pdf-import/346/editing-scans/)。辨識後在編輯器保留原始掃描。 | 原圖與可編輯結果可同時參照，提供放大，不把辨識完成視為校對完成。 |
| ScanScore | [官方 FAQ](https://scan-score.com/en/faq/)、[官方示範](https://scan-score.com/en/introducing-scanscore/)。掃描、修改、轉調、輸出，原圖可對照。 | 以「整理照片 → 辨識 → 校對 → 保存」為完整流程，錯誤要指出可採取的動作。 |
| PhotoScore | [原廠介紹](https://www.neuratron.com/photoscore.htm)。提供印刷及手寫音樂辨識，屬不同引擎能力範圍。 | 不將競品的手寫能力當成本專案已具備；目前 Audiveris 流程仍以印刷五線譜為主。 |
| Audiveris | [官方使用者編輯文件](https://audiveris.github.io/audiveris/_pages/guides/ui/README/)。文件明確指出完全無誤辨識仍不容易，提供符號與關聯校正。 | 保留詳細診斷、MusicXML 與進階校對入口。針對明確的低線距錯誤做有限重試，避免無限制反覆跑辨識。 |
| Dolce／jpeditor | [維護者專案說明](https://github.com/lodebar2026/dolce)。文字與譜面同步、格式轉換、OMR 的方向值得參考；本次未實機驗證其準確度與效能。 | 可視化與開放格式值得保留；不據此承諾完整格式相容或相同辨識品質。 |

另確認 [Finale 已停止開發與銷售](https://www.makemusic.com/press-room/press-releases-2024/makemusic-sunsets-finale/)，不作為新使用者的主要遷移目標。

## 目前觀察到的具體問題

1. 品牌側欄與標語佔據較多空間。Windows 150% 縮放下，有效可用空間更少。
2. 音符大卡片按固定寬度換行，視覺上不容易辨認完整小節與樂句。
3. 只能單選；「複製」實際上是立即重複一個音符，與一般剪貼簿習慣不同。
4. 插入位置依選取音符決定，缺少明確的模式與游標提示。
5. 修改音符需再按套用；多音符修改沒有一致的操作。
6. 背景排版期間整個編輯區被鎖住，輸入容易被中斷。
7. 每次修改重建全部清單項目，復原歷史重複序列化整份樂譜。
8. 照片錯誤先呈現 INFO 紀錄，真正原因不易閱讀。
9. 小圖片僅寫入 300 DPI 標籤不會增加實際譜線像素；需要根據辨識引擎量測做針對性處理。
10. 專業二胡記號可由上游排版，但目前視覺工具不足；必須區分排版能力與使用者可直接操作的能力。

## 選定的工作區

- 頂部：新建、開啟、復原／重做、儲存、匯出。品牌只保留小標題。
- 譜面上方：曲名、調號、拍號、速度；新增／修改模式、插入位置、常用音符與時值。
- 中央：按小節組織的譜面，可縮放、捲動、跳到小節、選取一段音符。
- 選取區：選取數量與位置；只修改使用者改動的屬性，保留其他音符差異。
- 二胡記號：按需要展開的弓法、指法、滑音、力度與連線工具。
- 右側：正式 PDF、原圖校對、作業訊息。可以切換專心編輯或閱讀預覽。
- 紙張設定：A4／Letter、譜字大小與每行小節數；正式輸出以 LilyPond 實際結果為準。

以 EOP 的熟悉程度降低學習成本，同時採用清楚的選取、多音符操作與可收合工作區。這是本專案的適配方案，不照搬某個產品的全部視覺外觀或功能密度。

## 效能與驗證方式

舊版基準：2026-11-12、Linux 開發環境、Qt offscreen、停用外部排版；以 1,000／10,000 個重複四分音符，修改中間音符並處理 Qt 事件，連做 5 次。中位數約 41.3／431.6 毫秒。這是開發環境基準，不是使用者 i3 電腦的承諾，也不是複雜樂譜全面測試。

改進方向：不可變音符與共享快照、可見範圍繪製、快取幾何位置、合併連續預覽要求、辨識與排版在背景執行。參考 [Qt Model/View 文件](https://doc.qt.io/qtforpython-6.11/overviews/qtwidgets-model-view-programming.html)，仍需以本程式實測確認效果。

驗收包括：範圍選取、複製貼上、撤銷重做、混合屬性、節奏錯誤定位、未儲存保護、舊檔相容、中文路徑、不同視窗與顯示縮放、真正 PDF／MIDI 與 Windows 安裝／更新。

## 版本狀態

- **0.1.2 已發布**：低解析度自適應重試、中文診斷與紀錄複製、照片預覽減負、小視窗可捲動、使用者版 README。
- **後續更新功能版**：程式內檢查、下載、校驗與原位置升級；需要實測後才發布。
- **0.2 UI 重整進行中**：以上新工作區與二胡工具；尚未列為 0.1.2 已完成功能。
- **仍需後續評估**：完整 EOPN 相容、多聲部圖形編輯、三連音／弱起／倚音的完整視覺操作、直接在正式 PDF 上編輯、進階弓法變體、所有教材記號變體。上游支援不代表目前 GUI 已支援。
