"use client";
import { useState } from "react";
import { useLocale } from "@numueg/theme-sdk";
import { X } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";

const NbAnnouncementBar = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const text = asString(s.text) || localized(locale, "🎉 Free shipping on orders over 500 EGP", "🎉 شحن مجاني للطلبات فوق 500 جنيه");
  const linkUrl = asString(s.link_url, "");
  const linkText = asString(s.link_text) || localized(locale, "Shop now", "تسوق الآن");
  const dismissible = s.dismissible ?? true;
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  return (
    <div className="nb-announcement relative py-2.5 px-4 text-center text-sm font-medium">
      <div className="container mx-auto flex items-center justify-center gap-2">
        <span>{text}</span>
        {linkUrl && (
          <a href={linkUrl} className="underline underline-offset-2 font-bold hover:opacity-80 transition-opacity">
            {linkText}
          </a>
        )}
      </div>
      {dismissible && (
        <button onClick={() => setDismissed(true)} className="absolute top-1/2 -translate-y-1/2 end-3 p-1 rounded-full hover:bg-white/10 transition-colors" aria-label="Dismiss">
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
};

export default NbAnnouncementBar;
