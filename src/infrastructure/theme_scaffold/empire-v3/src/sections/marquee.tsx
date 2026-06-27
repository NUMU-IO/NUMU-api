import { useShop } from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useT } from "../lib/i18n";

interface MarqueeSettings {
  text?: string;
  repeat?: number;
}

/** Black scrolling ticker — bold uppercase text alternating with the store
 *  name, separated by dots. Pure CSS animation (respects reduced-motion). */
export default function Marquee({ id, settings }: EmpSectionProps) {
  const s = settings as MarqueeSettings;
  const t = useT();
  const shop = useShop();
  const text = s.text ?? t("100% Independent", "100% مستقل");
  const storeName = shop?.name || "EMPIRE";
  const repeat = Math.max(4, Math.min(20, (s.repeat as number) || 10));

  const items = Array.from({ length: repeat }, (_, i) => (
    <span className="empire-marquee__item" key={i}>
      <span className="empire-marquee__text">{text}</span>
      <span className="empire-marquee__dot">●</span>
      <span className="empire-marquee__sub">{storeName}</span>
      <span className="empire-marquee__dot">●</span>
    </span>
  ));

  return (
    <div className="empire-marquee">
      <EditableText
        as="span"
        sectionId={id}
        settingId="text"
        value={text}
        style={{ display: "none" }}
      />
      <div className="empire-marquee__track">
        {items}
        {items}
      </div>
    </div>
  );
}
