        )

        # OKX 最後一根通常是尚未收盤的 K 棒。
        # 只分析倒數第二根，避免盤中訊號反覆出現/消失。
        closed_df = df.iloc[:-1].copy()

        if len(closed_df) >= 100:
            check_signal_and_notify(
                symbol_short,
                timeframe,
                closed_df,
            )

        logging.info(
            "[%s %s] 更新完成，資料 %s 根，台灣現在時間 %s",
            symbol_short,
            timeframe,
            len(df),
            taiwan_time_text(),
        )

    except Exception:
        logging.exception("[%s %s] 抓取或處理失敗", symbol_short, timeframe)

        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

        if exchange is not None:
            try:
                exchange.close()
            except Exception:
                pass


# ============================================================
# 10. 多執行緒工作
# ============================================================

def run_all_tasks():
    tasks = []

    for symbol_name, table_name, symbol_short in TARGETS:
        for timeframe in TIMEFRAMES:
            tasks.append(
                (symbol_name, table_name, symbol_short, timeframe)
            )

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(
                fetch_and_process_symbol,
                symbol_name,
                table_name,
                symbol_short,
                timeframe,
            )
            for symbol_name, table_name, symbol_short, timeframe in tasks
        ]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                logging.exception("工作執行緒發生未處理錯誤")


# ============================================================
# 11. 主程式
# ============================================================

def main():
    validate_config()

    logging.info("====================================================")
    logging.info("BTC / ETH AI 智能交易訊號監控系統啟動")
    logging.info("時區：Asia/Taipei（台灣時間 UTC+8）")
    logging.info("目前台灣時間：%s", taiwan_time_text())
    logging.info("監控週期：%s", ", ".join(TIMEFRAMES))
    logging.info("====================================================")

    # 啟動時先測試 Discord；測試失敗不終止行情監控，
    # 但請查看錯誤訊息並修正 Webhook。
    test_discord()

    try:
        while True:
            cycle_start = time.monotonic()

            logging.info("開始新一輪監控，台灣時間：%s", taiwan_time_text())
            run_all_tasks()

            elapsed = time.monotonic() - cycle_start
            sleep_seconds = max(1.0, LOOP_INTERVAL_SECONDS - elapsed)

            logging.info(
                "本輪完成，耗時 %.1f 秒；%.1f 秒後開始下一輪",
                elapsed,
                sleep_seconds,
            )

            time.sleep(sleep_seconds)

    except KeyboardInterrupt:
        logging.info("收到手動停止指令，程式結束。")
        sys.exit(0)


if __name__ == "__main__":
    main()
