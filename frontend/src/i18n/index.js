import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import en from "./locales/en.json";
import hi from "./locales/hi.json";
import mr from "./locales/mr.json";
import bn from "./locales/bn.json";
import te from "./locales/te.json";
import ta from "./locales/ta.json";
import gu from "./locales/gu.json";
import kn from "./locales/kn.json";

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: "en",
    interpolation: { escapeValue: false },
    resources: {
      en: { translation: en },
      hi: { translation: hi },
      mr: { translation: mr },
      bn: { translation: bn },
      te: { translation: te },
      ta: { translation: ta },
      gu: { translation: gu },
      kn: { translation: kn },
    },
  });

export default i18n;