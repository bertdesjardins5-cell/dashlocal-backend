import { fonts, makeStyles } from "@/src/theme";

// Shared DashLocal brand styles for sign-in forms.
export const useBrandForm = makeStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.brandSoftGray },
  wrap: { padding: 24, alignItems: "center" },
  card: { width: "100%", maxWidth: 440, backgroundColor: c.surface, borderRadius: 20, padding: 24, gap: 10, borderWidth: 1, borderColor: c.brandLine },
  title: { fontFamily: fonts.brandHeading, fontSize: 26, color: c.brandCharcoal },
  hint: { fontFamily: fonts.brandBody, fontSize: 15, lineHeight: 22, color: c.brandMuted, marginBottom: 6 },
  label: { fontFamily: fonts.brandBodyBold, fontSize: 14, color: c.brandCharcoal, marginTop: 4 },
  input: { height: 52, borderRadius: 12, backgroundColor: c.brandSoftGray, borderWidth: 1, borderColor: c.brandLine, paddingHorizontal: 16, fontFamily: fonts.brandBody, fontSize: 16, color: c.brandCharcoal },
  pin: { letterSpacing: 8, fontSize: 22 },
  err: { backgroundColor: c.brandCream, borderLeftWidth: 4, borderLeftColor: c.brandRed, borderRadius: 8, padding: 12 },
  errText: { fontFamily: fonts.brandBody, color: c.brandCharcoal, fontSize: 14 },
}));
