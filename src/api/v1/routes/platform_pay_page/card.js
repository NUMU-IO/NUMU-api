// NUMU platform card page. Framed by the merchant hub; the card typed here is
// posted straight to Kashier's Direct API and never reaches NUMU's servers.
//
// The hub passes {params, parent, lang, amount} as base64 JSON in the URL
// fragment (never sent to any server). params = the signed order from the
// API ({endpoint, hash, body}); the Kashier-Hash pins the amount, and the
// endpoint must be one of Kashier's card hosts whatever the fragment says.
// Outcomes go back to the hub by postMessage; the hub then polls our API,
// where the platform webhook is the source of truth.
(function () {
  "use strict";

  var FEP_HOSTS = ["fep.kashier.io", "test-fep.kashier.io"];
  var PARENT_ORIGIN = /^(https:\/\/([a-z0-9-]+\.)*numueg\.app|http:\/\/localhost(:\d+)?)$/;
  var TEXT = {
    en: {
      secure: "Secure checkout",
      heading: "Pay by card",
      number: "Card number",
      cards: "Visa / Mastercard",
      expiry: "Expiry",
      cvcHint: "3–4 digits",
      name: "Name on card",
      namePlaceholder: "As shown on your card",
      pay: "Pay ",
      encrypted: "Bank-level encryption",
      notStored: "Card details never stored",
      note: "Your card details go encrypted straight to our payment processor. We never store them.",
      threeDs: "Complete the verification from your bank:",
      processing: "Processing...",
      failed: "Payment failed. Check your card details.",
      unreachable: "Could not reach the payment gateway.",
      declined: "Card verification was not completed.",
      broken: "Payment is misconfigured. Close this window and try again.",
    },
    ar: {
      secure: "دفع آمن",
      heading: "الدفع بالبطاقة",
      number: "رقم البطاقة",
      cards: "فيزا / ماستركارد",
      expiry: "تاريخ الانتهاء",
      cvcHint: "3–4 أرقام",
      name: "الاسم على البطاقة",
      namePlaceholder: "كما يظهر على البطاقة",
      pay: "ادفع ",
      encrypted: "تشفير بمستوى البنوك",
      notStored: "لا نحتفظ ببيانات البطاقة",
      note: "تُرسل بيانات بطاقتك مشفّرة مباشرة إلى معالج الدفع ولا نحتفظ بها.",
      threeDs: "أكمل التحقق من بنكك:",
      processing: "جارٍ المعالجة...",
      failed: "تعذر إتمام الدفع. تأكد من بيانات البطاقة.",
      unreachable: "تعذر الاتصال ببوابة الدفع.",
      declined: "لم يكتمل التحقق من البطاقة.",
      broken: "إعداد الدفع غير صالح. أغلق النافذة وحاول مرة أخرى.",
    },
  };

  var $ = function (id) { return document.getElementById(id); };
  var digits = function (v) { return v.replace(/\D/g, ""); };

  var cfg = null;
  try {
    cfg = JSON.parse(decodeURIComponent(escape(atob(decodeURIComponent(location.hash.slice(1))))));
  } catch (e) {
    cfg = null;
  }
  // Keep the order out of the page history once read.
  history.replaceState(null, "", location.pathname);

  var t = TEXT[cfg && cfg.lang === "ar" ? "ar" : "en"];
  if (cfg && cfg.lang === "ar") document.documentElement.dir = "rtl";
  document.querySelectorAll("[data-t]").forEach(function (el) {
    el.textContent = t[el.getAttribute("data-t")];
  });
  document.querySelectorAll("[data-placeholder]").forEach(function (el) {
    el.placeholder = t[el.getAttribute("data-placeholder")];
  });

  var endpointHost = "";
  try { endpointHost = new URL(cfg.params.endpoint).hostname; } catch (e) { endpointHost = ""; }
  var parentOk = !!cfg && PARENT_ORIGIN.test(cfg.parent || "") && window.parent !== window;

  function post(msg) {
    if (!parentOk) return;
    msg.source = "numu-pay";
    window.parent.postMessage(msg, cfg.parent);
  }

  function show(text, isError) {
    $("card-form").hidden = true;
    $("three-ds").hidden = true;
    $("three-ds").textContent = "";
    var status = $("status");
    status.hidden = false;
    status.className = isError ? "msg err" : "msg";
    status.textContent = text;
  }

  function fail(text) {
    show(text, true);
    post({ status: "failed", message: text });
  }

  if (!parentOk || FEP_HOSTS.indexOf(endpointHost) === -1 || !cfg.params.hash) {
    show(t.broken, true);
    return;
  }

  var number = $("cc-number");
  var exp = $("cc-exp");
  var csc = $("cc-csc");
  var name = $("cc-name");
  var payBtn = $("pay");
  var payLabel = $("pay-label");
  var cardBrand = $("card-brand");
  // Set when the API signed the order to save the card (plan auto-renew):
  // Kashier returns the card token in its response, and the hub stores it
  // through our API once the payment is confirmed.
  var saved = null;
  $("amount").textContent = cfg.amount || "";
  payLabel.textContent = t.pay + (cfg.amount || "");

  function brand(cardNumber) {
    var d = digits(cardNumber);
    if (/^4/.test(d)) return "VISA";
    if (/^(5[1-5]|2[2-7])/.test(d)) return "MC";
    if (/^3[47]/.test(d)) return "AMEX";
    if (/^6(?:011|5)/.test(d)) return "DISC";
    return "CARD";
  }

  function parts() {
    var e = exp.value.split("/");
    return { mm: digits(e[0] || ""), yy: digits(e[1] || "") };
  }

  function valid() {
    var p = parts();
    return (
      digits(number.value).length >= 13 &&
      Number(p.mm) >= 1 && Number(p.mm) <= 12 && p.yy.length === 2 &&
      digits(csc.value).length >= 3 &&
      name.value.trim().length > 1
    );
  }

  function refresh() { payBtn.disabled = !valid(); }

  number.addEventListener("input", function () {
    number.value = digits(number.value).slice(0, 19).replace(/(.{4})(?=.)/g, "$1 ");
    cardBrand.textContent = brand(number.value);
    refresh();
  });
  exp.addEventListener("input", function () {
    var d = digits(exp.value).slice(0, 4);
    exp.value = d.length > 2 ? d.slice(0, 2) + "/" + d.slice(2) : d;
    refresh();
  });
  csc.addEventListener("input", function () { csc.value = digits(csc.value); refresh(); });
  name.addEventListener("input", refresh);

  // Kashier's 3-D Secure frame reports its outcome by postMessage.
  window.addEventListener("message", function (event) {
    var host = "";
    try { host = new URL(event.origin).hostname; } catch (e) { return; }
    if (host !== "kashier.io" && !/\.kashier\.io$/.test(host)) return;
    var data = event.data;
    if (typeof data === "string") {
      try { data = JSON.parse(data); } catch (e) { return; }
    }
    if (!data || data.message !== "merchantStoreRedirect") return;
    if (data.params && data.params.status === "SUCCESS") {
      show(t.processing, false);
      post({ status: "submitted", saved: saved });
    } else {
      fail(t.declined);
    }
  });

  $("card-form").addEventListener("submit", function (event) {
    event.preventDefault();
    if (!valid()) return;
    var p = parts();
    var body = JSON.parse(JSON.stringify(cfg.params.body));
    body.paymentMethod = {
      type: "CARD",
      card: {
        number: digits(number.value),
        nameOnCard: name.value.trim(),
        expiry: { month: ("0" + Number(p.mm)).slice(-2), year: p.yy },
        securityCode: digits(csc.value),
        save: false,
        enable3DS: true,
      },
    };
    var cardExtra = cfg.params.card_extra;
    if (cardExtra) {
      Object.keys(cardExtra).forEach(function (k) { body.paymentMethod.card[k] = cardExtra[k]; });
    }
    var last4 = digits(number.value).slice(-4);
    // The card is not kept in the page once it is sent.
    number.value = "";
    csc.value = "";
    payBtn.disabled = true;
    show(t.processing, false);

    fetch(cfg.params.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Kashier-Hash": cfg.params.hash },
      body: JSON.stringify(body),
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (json) {
          return { ok: res.ok, json: json };
        });
      })
      .then(function (out) {
        var json = out.json || {};
        var r = json.response || json;
        var card = (r.paymentMethod && r.paymentMethod.card) || r.card || {};
        if (cardExtra && card.cardToken) {
          saved = {
            card_token: String(card.cardToken),
            agreement_id: (card.agreement && card.agreement.id) || r.agreementId || null,
            last4: last4,
          };
        }
        var redirectUrl = r.authentication && r.authentication.redirectUrl;
        if (redirectUrl) {
          var frameHost = "";
          try { frameHost = new URL(redirectUrl).hostname; } catch (e) { frameHost = ""; }
          if (!/(^|\.)kashier\.io$/.test(frameHost)) return fail(t.failed);
          $("status").hidden = true;
          var box = $("three-ds");
          box.hidden = false;
          var label = document.createElement("p");
          label.textContent = t.threeDs;
          var frame = document.createElement("iframe");
          frame.title = "3-D Secure";
          frame.src = redirectUrl;
          box.appendChild(label);
          box.appendChild(frame);
          post({ status: "three_ds" });
          return;
        }
        // ponytail: only redirectUrl is supported for 3-D Secure. Kashier's
        // redirectHtml fallback is an inline self-submitting form this page's
        // CSP (script-src 'self', form-action 'none') would block; add a
        // sandboxed handler if Kashier starts returning only that.
        var status = String(r.status || json.status || "").toUpperCase();
        if (out.ok && ["SUCCESS", "CAPTURED", "PAID"].indexOf(status) !== -1) {
          post({ status: "submitted", saved: saved });
          return;
        }
        var msg = (json.messages && json.messages[cfg.lang === "ar" ? "ar" : "en"]) || r.message;
        fail(msg || t.failed);
      })
      .catch(function () { fail(t.unreachable); });
  });
})();
