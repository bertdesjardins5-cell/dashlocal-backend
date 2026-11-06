// Staging-only Expo config. Copied into a SEPARATE copy of the frontend by build_staging.sh, never into frontend/.
// Installs next to the live app (different app ID and name) and talks only to the Railway staging server.
module.exports = ({ config }) => {
  const url = process.env.EXPO_PUBLIC_BACKEND_URL || "";
  if (!/^https:\/\/.+/.test(url)) throw new Error("Set EXPO_PUBLIC_BACKEND_URL to your https Railway staging address");
  if (/emergent\.host|emergentagent\.com/.test(url)) throw new Error("Staging build must not point at the live Emergent server");
  return {
    ...config,
    name: "DashLocal Staging",
    slug: "dashlocal-staging",
    ios: { ...config.ios, bundleIdentifier: "com.emergent.arrivewise.ue3on3.staging" },
    android: {
      ...config.android,
      package: "com.emergent.arrivewise.ue3on3.staging",
      googleServicesFile: undefined, // the live Firebase file only matches the live app ID; push alerts are off in staging
    },
    extra: { ...(config.extra || {}), stagingBackend: url },
  };
};
