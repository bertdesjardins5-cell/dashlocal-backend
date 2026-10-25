import React from "react";
import { Text, View } from "react-native";
import { Redirect, useRouter } from "expo-router";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSession } from "@/src/session";
import SignInForm from "@/src/components/SignInForm";
import BrandHeader, { Kicker } from "@/src/components/brand/BrandHeader";
import { fonts, makeStyles } from "@/src/theme";

// Merchant sign-in from the landing page -> the store's order management portal.
export default function MerchantSignIn() {
  const s = useStyles();
  const router = useRouter();
  const { ready, user } = useSession();
  if (ready && user?.role === "merchant" && user.merchant_id) return <Redirect href={`/merchant/${user.merchant_id}`} />;
  return (
    <View style={s.root} testID="merchant-landing-sign-in">
      <BrandHeader />
      <KeyboardAwareScrollView contentContainerStyle={s.wrap} keyboardShouldPersistTaps="handled" bottomOffset={24}>
        <View style={s.intro}>
          <Kicker>Merchant portal</Kicker>
          <Text style={s.lead}>Upload orders, track every drop and see your weekly bill.</Text>
        </View>
        <SignInForm scroll={false} role="merchant" title="Sign in to your store" hint="Use the email and password DashLocal set up for your store. Need an account? Ask your DashLocal admin."
          onSignedIn={(u) => u.merchant_id && router.replace(`/merchant/${u.merchant_id}`)} />
      </KeyboardAwareScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.brandSoftGray },
  wrap: { padding: 24, alignItems: "center", gap: 20 },
  intro: { width: "100%", maxWidth: 440, gap: 6 },
  lead: { fontFamily: fonts.brandBody, fontSize: 17, lineHeight: 26, color: c.brandCharcoal },
}));
