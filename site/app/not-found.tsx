import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/600.css";
import "./globals.css";
import messages from "@/messages/en.json";
import localeList from "@/i18n/locales.json";

// Served as 404.html by the static host for any unknown path.
export default function NotFound() {
  const t = messages.notFound;
  return (
    <html lang={localeList.defaultLocale}>
      <body>
        <main id="main" className="wrap py-24">
          <h1 className="display">{t.title}</h1>
          <p className="lead mt-6">{t.body}</p>
          <p className="mt-6">
            <a className="link inline-flex min-h-11 items-center font-medium" href={`/${localeList.defaultLocale}/`}>
              {t.home}
            </a>
          </p>
        </main>
      </body>
    </html>
  );
}
