import {
  useState } from "react";
import { useProductSizeChart,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";

interface SizeChartSettings {
  trigger_label?: string;
  title?: string;
}

/**
 * Size guide. Resolution (product `custom` → store `default` → `off`) is owned
 * by the SDK's `useProductSizeChart()` so every theme resolves it identically
 * to the merchant hub + backend validator. Renders a trigger pill + modal
 * table; returns null when there's nothing to show.
 */
export default function SizeChart({ id, settings }: EmpSectionProps) {
  const s = settings as SizeChartSettings;
  const [open, setOpen] = useState(false);
  const chart = useProductSizeChart();

  if (!chart) return null;
  const unit = chart.unit ? ` (${chart.unit})` : "";

  return (
    <div className="empire-container" style={{ paddingBottom: "1rem" }}>
      <button
        className="empire-sizeguide__trigger"
        type="button"
        onClick={() => setOpen(true)}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M3 7h18v10H3z" />
          <path d="M7 7v4M11 7v6M15 7v4M19 7v6" />
        </svg>
        <EditableText
          as="span"
          sectionId={id}
          settingId="trigger_label"
          value={s.trigger_label || "دليل المقاسات"}
        />
      </button>

      {open ? (
        <div
          className="empire-modal"
          role="dialog"
          aria-modal="true"
          aria-label={s.title || "دليل المقاسات"}
        >
          <div className="empire-modal__overlay" onClick={() => setOpen(false)} />
          <div className="empire-modal__panel">
            <div className="empire-modal__head">
              <h2 className="empire-modal__title">
                {(s.title || "دليل المقاسات") + unit}
              </h2>
              <button
                className="empire-drawer__close"
                type="button"
                onClick={() => setOpen(false)}
                aria-label="إغلاق"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="empire-modal__body">
              {chart.image_url ? (
                <img
                  className="empire-sizeguide__img"
                  src={chart.image_url}
                  alt=""
                />
              ) : null}
              <table className="empire-sizetable">
                <thead>
                  <tr>
                    <th>المقاس</th>
                    {chart.column_headers.map((h, i) => (
                      <th key={i}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {chart.rows.map((row, ri) => (
                    <tr key={ri}>
                      <th scope="row">{row.size}</th>
                      {chart.column_headers.map((_, ci) => (
                        <td key={ci}>{row.values[ci] ?? "—"}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {chart.notes ? (
                <p className="empire-muted" style={{ fontSize: "0.8125rem", marginTop: "1rem" }}>
                  {chart.notes}
                </p>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
