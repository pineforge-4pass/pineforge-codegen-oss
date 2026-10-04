import { hasLocale, IntlErrorCode } from "next-intl";
import { getRequestConfig } from "next-intl/server";
import { routing } from "./routing";

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;
  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
    // A message that does not compile or misses a value fails the build
    // instead of shipping the key.
    onError(error) {
      if (error.code === IntlErrorCode.FORMATTING_ERROR || error.code === IntlErrorCode.INVALID_MESSAGE) throw error;
      console.error(error);
    },
  };
});
