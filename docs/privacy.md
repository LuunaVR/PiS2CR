# Privacy Policy: Pass The Controller

Last updated: 12 November 2026

"Pass The Controller" is a Twitch extension. It lets viewers press buttons and move sticks on a shared on-screen controller while a streamer plays. It is made by Luuna (LuunaVR). This page explains what information it uses.

## What It Uses

**Without any action from you**
- An anonymous Twitch viewer ID. Twitch generates it, it is specific to this extension, and it does not reveal who you are.
- Your role on the channel (viewer, moderator or broadcaster).
- The buttons and sticks you press, and when you press them.

**Only if you choose Share My Identity**
- Your real Twitch user ID. Twitch asks for your permission first. The extension never sees your password.

The extension does not ask for your name, email address, location or any other personal details.

## Why It Is Used

- To let the streamer's setup apply its rules: who may play, how many people at once, and how long a button may be held.
- To pass your presses to the streamer's controller in real time.
- To show the streamer who is playing. If you shared your identity, the streamer's setup looks up your Twitch username and display name, and checks whether you follow, subscribe, or are a VIP, using Twitch's own service and the streamer's authorization. People who have not shared their identity are not named.

## Where It Goes

1. **Twitch** runs the extension page and handles sign-in and identity sharing.
2. **A relay hosted on Cloudflare** passes messages between your browser and the streamer's setup. It does not store your presses or identity. Like any web host, Cloudflare may process technical connection data such as your IP address under its own policies.
3. **The streamer's Raspberry Pi** receives your presses. It keeps a short recent-activity list and a name cache in memory only, and both are cleared when it restarts. The streamer may save a list of allowed Twitch usernames in their own settings.

## What It Does Not Do

- No advertising, tracking, profiling or analytics.
- No cookies or local storage in your browser.
- No selling or sharing of your information.

## Keeping And Deleting

Nothing about viewers is stored permanently by the extension. You can stop sharing your identity at any time in your Twitch extension settings. Closing the extension ends your connection to the relay.

## Children

The extension follows Twitch's age requirements and is not meant for anyone under 13.

## Contact

Questions or requests: open an issue at <https://github.com/LuunaVR/PiS2CR/issues> or use the support email on the extension's Twitch page.

## Changes

If this policy changes, the date above will change. The latest version is always at <https://github.com/LuunaVR/PiS2CR/blob/main/docs/privacy.md>.
