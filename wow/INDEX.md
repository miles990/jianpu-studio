# wow artifact index

本專案所有 wow 過程紀錄都在 `wow/`。成品放產品自己的路徑（`docs/`、`examples/`、套件目錄、README 資源）；本索引指向真實路徑，不要在 `wow/` 再複製一份。

## Deliverables（真實路徑，不複製進 wow/）

| Deliverable | Real path | Status |
|---|---|---|
| （產品本體、文件、示範） |  |  |

## Process records（進 git）

| path | what |
|---|---|
| `wow/INDEX.md` | 本索引 |
| `wow/spec.md` | 對外規格 |
| `wow/progress.md` | 進度、資源宣告、贏面表 |
| `wow/reviews.md` | 評審判決摘要 |
| `wow/proof/` | 策展後的最終證據 |
| `wow/calibration.md`、`wow/judge-criteria.md` | 若有使用 |

## Intermediate（gitignore，在 wow/work/）

| path | what |
|---|---|
| `wow/work/briefs/` | 派工簡報（`brief.md` 與各 agent prompt） |
| `wow/work/runs/` | checks、judge、websmoke、feedback 輸出 |
| `wow/work/reviews-raw/` | 評審／法官原始逐字稿 |
| `wow/work/evidence/` | 原始截圖、擷取、trace |
| `wow/work/agent-runs/` | wow-agent 執行資料夾 |
| `wow/work/holdout/` | 保留樣本／答案卷。建造者在盲測前不得讀取 |
| `wow/work/blind/` | 場外盲測沙盒的策展副本（現場沙盒不在 repo 內） |
| `wow/work/logs/` | agent 進度 log |

## Live paths outside the repo

| what | path | note |
|---|---|---|
| Blind live sandbox | （執行時記入） | 預設 `<tmpdir>/wow-blind/`（`WOW_BLIND_ROOT` 可改）。必須在 git 樹之外；策展結果複製到 `wow/work/blind/`。 |
