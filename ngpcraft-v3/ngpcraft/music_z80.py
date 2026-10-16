"""The music on the sound CPU (ngpc.engine_speedups, docs/PLAN_MUSIQUE_Z80.md).

Applied to the final audio.c when the selected driver is one of the variants transcribed in
music_z80_asm.py (VARIANTS: the text of each function, by SHA-256), no stream turns an effect
on (F1 F2 F3 F9 FA, FE 01, FE 02; FC with pitch curves) and the Z80 program fits. Only the
effects the instruments use are assembled. Otherwise audio.c is unchanged (the report says why).

What changes in audio.c:
- the variables both CPUs use are placed in the Z80's RAM (SHARED_C, same byte layout);
- Bgm_Update, Bgm_Stop and Bgm_StartLoop4Ex post their work to the Z80's mailbox, in order;
  the TLCS-900 waits for the Z80 to be done before touching the shared state again
  (ngc_audio_service, Sfx_Stop, Bgm_Stop, Bgm_StartLoop4Ex);
- the voice streams are copied into the Z80's rings (ngc_z80_refill), the loop followed;
- the Z80 program replaces the relay (it still serves it for Psg_ResetBases);
- the driver code only the Z80 now runs is removed (closure over the references).
"""
import hashlib
import re

from .compiler import CompileError
from .music_z80_asm import (SHARED_LAYOUT, V, MB_STEP, MB_STOP, MB_START, MB_CONTINUE, INSTRUMENT_COLUMNS,
                            layout, source)
from .z80asm import assemble

FUNCTION = re.compile(r"^static [^\n;=]*?\b(\w+)\(([^)]*)\)\n\{\n.*?^\}\n", re.M | re.S)

# audio.c functions whose behaviour music_z80_asm.py transcribes (selected driver 0:4, gate and
# master volume never changed), by the SHA-256 of their text.
TRANSCRIBED = {
    "Bgm_Update": "d0805d9fd5d8bf338885916cd33c22e22835bdf34099f9f458017653478c493b",
    "Bgm_Stop": "c8dcf297840eb2258b999fbd22f23764b0bf004ca81974f73be3a8e06a9b4979",
    "Bgm_StartLoop4Ex": "50ea93587773a727929fbb2ec00ca306f78bb922fd40b8b5aac332e49ac8b725",
    "Bgm_ResetFadeState": "7536ab5bdff2fa58010863a1682d890486ffcd32e05d3fed9cb24dc572a2ce84",
    "Bgm_ClearRestoreFlags": "7f73ab966d941b5cba76aa2dc11202c06e9de3f93d96b74e2bc49666e604f6ed",
    "BgmVoice_Step": "fc2ff2e03d95c327b4c979649ddb29d4526021cca2c11ec39b928b6c08f4a0a7",
    "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203",
    "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37",
    "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1",
    "BgmVoice_CommandFromState": "357ed059272f013590b960a412d069a418b027a897da85b1de0d4a980d62d9a3",
    "BgmVoice_CommandSilence": "b311d8fd434157144ec2b71fd7aeda5e19694b89072e87ae2130d2142e930ef6",
    "BgmVoice_SetNote": "26a67fb305b401bf3db932a01cc0535d904640c2a44b3852db0741a111707120",
    "BgmNoteToDiv": "df686b23215b791d83656760a082f70dca12ea9b76c4d234b9808f4abb8a2e00",
    "BgmVoice_ApplyInstrument": "09d7818158d766b97ab32cfd094d8dd83ee6db415e91f8d1ba3e8a5a356cb615",
    "BgmVoice_ResetFx": "6b5930a882404d8ebbb16ab347673119378bdc4a6dfb2a0a327995ff5b32174f",
    "BgmVoice_Reset": "da2e7facb5f8f70dbdfdd21e5b759a2d04c1a3a767a68b89d6b5f72fb7fe8ffb",
    "BgmVoice_StartEx": "5220948f0715e82018d1228b7d7ef1a6a090fc9400970c89bd189233436882cc",
    "BgmVoice_Stop": "1da0ad635160c7b346aa2c2d361c2eb0cd1e8be6ac9c3a61cf40456f9c87024e",
    "BgmVoice_UpdateFx": "5852132dc8d02f62f8f520d4fec313b39e498c07646832d2cecb2103ec523096",
    "BgmVoice_MacroReset": "3923da08ccfb86a4a31b8210bdf44eb24e23fbf889bbc664c1cccfeb555bec78",
    "BgmVoice_MacroTick": "e9813834e72feacdb828f16e3b306c9d364e9a5eac7d3861a1a4646a2c0a3ff7",
    "MakeNoiseCmd": "b7a02b917b9a37e67c2b25b62c025d576078fdb4bcc685c82bc72812cf2ffe02",
    "MakeNoiseCmdFromNote": "cd5232c79558e1ed4cf1c5086f9d91207595cce02165aad5392c7945958bae7b",
    "MakeSilenceCmd": "5037c1e4c6b2b5ca1dd620b8e108fad77652d78357f79cc613b83622aebfc3a6",
    "LastQueuedOrCommitted": "980b01652c4d0e99ab730cef3f3f593c685c6a49ee7fd73fde51b17c745bbfde",
    "CommitPendingShadows": "8edb53816f6c8db9bdc643b6f97357c1417f5ea527d9c61116a0745da3023e15",
    "BufferReplayPending": "488742bbef83a6bdc134f3a123c531779e589e78b5abb38a201db2e46895f3ea",
    "BufferBegin": "d6351986e37e7753d1d17d38cc4878c37c7c0eb20846877c12d62afb5f7385d7",
    "BufferPush": "b5f0e606ba1fca42d6678993a974e623e1eb81dfc53bf6300db735c191ffd430",
    "BufferPushIfSpace": "db31398902b34e7f76a0ed231cbc5bcc7cf241bbeb601e5a6cd0fe0590f77ad7",
    "BufferPushIfChanged": "1e939c9ab9b0513fa61bb6277c6d05dd6a30f0ff58de598f2e20c99b6046576f",
    "BufferPushBytesIfChanged": "2d20c93430c63053576114367d88a12ec4f4461c54b12278d98cb080a86253f0",
    "BufferCommit": "ebd011b121b272bbcd14d26ce246fa24716a69c301acf2bdba372ebb750253b1",
    "WaitBufferFree": "8eb3e0dacf4982ec1d26b7da986bfb7de27e400293684449a47c422a1b923226",
    "Sounds_ResetState": "6a8fc4252f27b4d7ecd36530f3198705eb2d47c4868370c54d5118a654943a22",
    "Sounds_Init": "2592402cc92f42127af3b5a9f9a1ab02f7296a44d0f30381c8476a36ece86ca4",
    "Bgm_SetGate": "ad667bb95c9b2f01f00925b6acbf7b8ef77b4d2a133e62368c17f08efde0706d",
}
# The driver variants transcribed, "factory:updates" (music_library.select_runtime): factory
# bits 1 envelope curves, 2 pitch curves, 4 macros (their tables; BgmVoice_UpdateFx, MacroTick
# and MacroReset read them; FB / FC / FD select one in a stream); updates 0 (Sound Creator songs)
# or 1 + level 1 + divider 2 + noise note 4 (held streams: FE 03, and FE 04 / 05 / 06). Each
# variant: the functions of 0:0 (_NATIVE) with these texts instead (generated from the frozen
# registry runtime/music_native.json, checked against the variants pinned before).
_NATIVE = {**{k: v for k, v in TRANSCRIBED.items() if not k.startswith("Ngc_Bgm")},
           "BgmVoice_Step": "de76a165744fcbf7d848dc3ce739379099d9f1a57b6ffb150d9424cf2b80d7c3"}
_DIFFERS = {
    "0:1": {"BgmVoice_Step": "573f729d6d124512584ed7cc4d414597393409acd0b86b37c57256199059ac95", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203"},
    "0:2": {"BgmVoice_Step": "69476cd666cfcb3f0a5ca4ee538be2eb6a2219a8496ab2320ac984ae497139b9", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37"},
    "0:3": {"BgmVoice_Step": "5108b2c393f7053ca5b2ad7917d37694cdeed453a8db9a85f9ebffc5166aa9d3", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1"},
    "0:4": {"BgmVoice_Step": "fc2ff2e03d95c327b4c979649ddb29d4526021cca2c11ec39b928b6c08f4a0a7", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1"},
    "0:5": {"BgmVoice_Step": "2840aa32d3b8ea8bf335f5ecb3b61b34e912aa23c9a4e672c2b7f83ad1011fc7", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "0:6": {"BgmVoice_Step": "fcc634a17ee324c15cb653175482fc18ec2e8bdc76cadca76f1f82b7c8e2c017", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "0:7": {"BgmVoice_Step": "3bfca0f419c12a2d21dd1d146badf22e3141c32ace0aa7572a284a7d4f7d3d0a", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "0:8": {"BgmVoice_Step": "deb09fd97e7356c5970d876d7d3bb95894fb8b07cb9e7db8046f4e71391789e2", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "1:0": {"BgmVoice_Step": "63ff1a05d93cd81547a94bf443e1d71368b94e5f8808324b55d928a1e76f7d28", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c"},
    "1:1": {"BgmVoice_Step": "598a3c599358a2aa4f72f87ce5ecc36a6a5b4f9d0c406d7a4e4145130cfba26e", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c"},
    "1:2": {"BgmVoice_Step": "fc316d79cc0b5575700e64f5825576c1f536d1cffdfa241cb46b77b73c81bcd8", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c"},
    "1:3": {"BgmVoice_Step": "918fbd630fb52666b2c58318b3a057219bbcce90d7836e239a7cc6ccf1b7cb0d", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c"},
    "1:4": {"BgmVoice_Step": "4fbca5b16b436f709dfbf7a63b850ebf958630fbaee4558565f2f1ae211b9aa4", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c"},
    "1:5": {"BgmVoice_Step": "ecd120de0923d26370876db2ac7f9763b7d53d87485e8dec29f6d95788c23075", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "1:6": {"BgmVoice_Step": "cdac8dbd2cb967f5705e10478a42d4e0db9b3fa764422d657605bfd2d71a0391", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "1:7": {"BgmVoice_Step": "962834def0ed80c2d220ec30b6e678917e1f833ccdab76aec58b6b8135e76074", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "1:8": {"BgmVoice_Step": "c85151d06d5b333422406d2d5d4c1191bd032969443891c347005cb01f711a2d", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "706c777e65bfbad0e0c24195252048558e577008fccbbc2673c573e489fae58b", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "2:0": {"BgmVoice_Step": "6eb86ed8cd269c865eeb672ed59e4f8c36b8b4eb0205c318dc168f17bd35c7b3", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271"},
    "2:1": {"BgmVoice_Step": "80ef178d301e11484133f1f49baccd1f384e208778b7abdb3a3392dd02276c28", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271"},
    "2:2": {"BgmVoice_Step": "486f91199660bad43414e1826ef345c05e191b5e2b78a5aafb980784fa9e09be", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271"},
    "2:3": {"BgmVoice_Step": "7c396ec3040cadc46bbba0f0edd33194dbb038deb43c3b4577ec11149fb59634", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271"},
    "2:4": {"BgmVoice_Step": "2ef6878893922d039112a1415c02103bc6ee7cec1f085ac5b983059186f735a4", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271"},
    "2:5": {"BgmVoice_Step": "e7fabafe14359892cbd017d49b65e94bbf86448aea591dc5eba593d3fb741de7", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "2:6": {"BgmVoice_Step": "cb0a1859c812eeb2dc9d036933c0b657bee7f157c9e30999844d2a4e80b3bcca", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "2:7": {"BgmVoice_Step": "1c5751e594f5e4807876eef07dcfed00c489b14cae059fcf6197ffcbf44aa535", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "2:8": {"BgmVoice_Step": "e6b773504778fd994c844f3621df4d20dbb30c60dc008c7b1e839645d9680307", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "8409bc67240a4f585cc4500109e032321033b40acd471c5bfe67d0a7ea1d3b2c", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "3:0": {"BgmVoice_Step": "41d60d28d3fd74801bcf37f5e324b65e99010a4f1cbba2c0c203f85300504634", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287"},
    "3:1": {"BgmVoice_Step": "a8000eae538ff1ed6a8dfaec131ffd264796e57eb8e7239f8f1e9699f6fa77c9", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287"},
    "3:2": {"BgmVoice_Step": "7f2cd6600538d54d8f37ce714b633ef93381e66bf24a6d08f78b162ca96832fd", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287"},
    "3:3": {"BgmVoice_Step": "6071a1d768b40152c8d29a93ad385c9a2af7ef32bac93ffd247fbad1a288f250", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287"},
    "3:4": {"BgmVoice_Step": "7e65d163c0dddb3bc3266965d9091f8518b2d8be9806ab0970451b892b9e6717", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287"},
    "3:5": {"BgmVoice_Step": "5be0f5dcd28934e7e5201ed58480d476e13482f353d79b8990b62083a72b138b", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "3:6": {"BgmVoice_Step": "75b36a9d06a79a5138416e2681fb874b1861969e1b0c11bb3df84a8f9827be62", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "3:7": {"BgmVoice_Step": "3abe9c97c7a7dfe180c5e49ee7ec97a9de9ebb5f7a8e1f0726091f8cd9463532", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "3:8": {"BgmVoice_Step": "0924c28f79a3291c793b6b37c13d83d94ed6d2e4a6be7ede2dc3cd9b60e98e5a", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "496b6848c31c11877afb12e626fa1150767637b21b3014f7e770b6fe19819f35", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "4:0": {"BgmVoice_Step": "1f90ffa7f712c41d9d9d71243a764f5bb6220e9153ea5a7625eceedb69b68240", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "4:1": {"BgmVoice_Step": "7528621368da108bdf8c9778c0148e8d64ea45c0b8d677ae3a12896d0a5c278a", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "4:2": {"BgmVoice_Step": "b954e3d2caa87072e9926360636495be24403a3b2175c0eb6c3770b39b69f9b1", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "4:3": {"BgmVoice_Step": "5b0e9475d0da16cb04188d9c44f42ed9685c7fbc9bee10160d7a63118cf985e2", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "4:4": {"BgmVoice_Step": "daee9761d5bd3bd3cc1c86f275951f5e3e0c6723eefed99bad1b6ceb32f0209f", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "4:5": {"BgmVoice_Step": "e26c9b177faab23564aad8734eb014dfe86c61f96febe05680bafd25d52a231d", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "4:6": {"BgmVoice_Step": "14554127eba8e37ad2510be974e56dd6da504e3d86b299cfb2d46f3b83e4c56a", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "4:7": {"BgmVoice_Step": "b5037e57cd471a6d1a5499a0d1ed2c7df0fe7bc776ad6efe0e80913820ecf688", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "4:8": {"BgmVoice_Step": "41f57fbbbf9d0d4a02dfbc4beed9293b8b6040bafe930ee726986ba43978aa98", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "ace5e7e624ab8c80479e14f9a218b224209f2a98eed561aaf3ddf2e80980e4d0", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "5:0": {"BgmVoice_Step": "3b59f6274e996ce8eaabdb26e30f830a3a1af566abaf8071c3f194cab97f34c9", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "5:1": {"BgmVoice_Step": "def100cb3b3b4338bdef3d153861befe1a1ec4237d2cff04859d8072ddbb6048", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "5:2": {"BgmVoice_Step": "a6cc25fd2ee260f4df14aa1dca263bd3db404e933b4c5a00caab48a92f267a4a", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "5:3": {"BgmVoice_Step": "1bff984d3f21a21aa0cfecfb146349728861484db3c85943599458ea6a779622", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "5:4": {"BgmVoice_Step": "4087e1bde3716cd457d4f71e02ebf31ba9fde6149e33e4da6373a7cbe0cf1765", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "5:5": {"BgmVoice_Step": "27cc60c827d319c7d9abc9119afca8d1b9f42394b24e4ea0a9c215a69450925e", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "5:6": {"BgmVoice_Step": "7fee46591926f7250b4a17e7d19ca0f164e497ff7cf6d1f309faed31daeef2f7", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "5:7": {"BgmVoice_Step": "d66a09579aab917553f8dccf155fdbd45f024106066ba67a059d8c1b5d36ad32", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "5:8": {"BgmVoice_Step": "d807585a94e3d567cc608a68548fb13652aa4469535637c857437a232d16fd9b", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_ApplyInstrument": "bf3558af9a7cd721f654bc423842a08a5ecda93eae6e385cac10e5cf34cde3df", "BgmVoice_UpdateFx": "018ab4cfb175b3d3be04bce8088f7eb81b88393e62a65e4af0527192baea795c", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "6:0": {"BgmVoice_Step": "04cc08d66e08ff0c6a6c90da303d202f65c051f4c910d0d5113eddffed4a402f", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "6:1": {"BgmVoice_Step": "435595dcf3fb8e8b309eb44c608401275f257bf09635cf925777198392106c23", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "6:2": {"BgmVoice_Step": "7abe98c021924dfc8278db359363e80d642d3727947d444d405293879c606b9d", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "6:3": {"BgmVoice_Step": "8eb513c466a3ebb64fd5ec95d90dd5bbbb73488875d833922135ad7b9b239684", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "6:4": {"BgmVoice_Step": "4b1b0d9759c9d66b21c55ce0ca152bd157cd7f7003c24617b4974eac9b53d321", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "6:5": {"BgmVoice_Step": "34462b9c26d87daa9413b23abe40a6b4f3558a218ee6cd9f8b64f061e28d8fbc", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "6:6": {"BgmVoice_Step": "f9d1d8e9e3b477acad823bc55de9a1511647bc510a3a3b6d502be9467faa2db4", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "6:7": {"BgmVoice_Step": "b4eb696856a481aa3204a84176de48bde8d3636f161705135c5a73e5d318b8f7", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "6:8": {"BgmVoice_Step": "e076f9143d82ffe1d601951ad56b8704f797866e4b09e114b65c60a7074a5c2c", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "db735444f70c72d785c426095caa14bbcc74260fa7bfcb5110aeeb68e668dd15", "BgmVoice_UpdateFx": "3f8800816a43ce245fc6423db59b15bed69d867eb69b55db6ceb6dc45b926271", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "7:0": {"BgmVoice_Step": "ea10de6aeeba1b600e4ee225fc06d8584e732eef547a6960bb2ef113d599fbb0", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "7:1": {"BgmVoice_Step": "fd481635b686e909b0510da4ec34ceee6f1c5a4731c3088fa83f9ed18591af17", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "7:2": {"BgmVoice_Step": "e16cb39f707ed1d8d735966652741f82a2ad5fca367e2aa3b2c780e59e1d2bd3", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "7:3": {"BgmVoice_Step": "9444c132c4568fcb0eb7055bbe6acf0d856c855656408905258c2e3845b2478b", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "7:4": {"BgmVoice_Step": "3f1e2822fcde72d335d233c1ac1cf89c3af31dd34a31006d6cb7864ef17ab528", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230"},
    "7:5": {"BgmVoice_Step": "d63387c4dcfa6c75d0db5d9faaa32254395964bc1ecb29967b6b4e6c6a023962", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "7:6": {"BgmVoice_Step": "e5057b33fec6d6b06ba6dee466a2f0f4c7cf9e79086973d2dbbabf87c4c91a3b", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "7:7": {"BgmVoice_Step": "fd7445d989ac6de62463c0e08ab0279a1b7ed1d4be658aae3e1fa8c5edb66d42", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
    "7:8": {"BgmVoice_Step": "9550b0c35a67bfe4adf156699e0a337527d819154563aa43a38ec5a4eccdcc00", "Ngc_BgmHold": "daba99bcd4447e51707513bbcffc5009819be74d4352682c6e033f811c4be203", "Ngc_BgmLevel": "24dc66b8d79ec46f02fa98affcdf6458eb32e111e79772f561a56dbb5b0bcc37", "Ngc_BgmDivider": "b19a24de8310255e451eafedceb5415dd709e140ed209caa5b34353dcb7728a1", "BgmVoice_CommandFromState": "062141870bd5c53aa6a9706c50b643fb561259fe9652a1837365322e3334422f", "BgmVoice_ApplyInstrument": "91c4b21b75d1a6e91c1ab8c7f9d74c70c7f6751c2b45bc1dcb457c48670843df", "BgmVoice_UpdateFx": "ca9a484992cbc38ec19cfbd7531f22d7e99e8e8ba435748987b9ac33e183d287", "BgmVoice_MacroReset": "9049c3fe1fed600e62bdda502407db55ad55ce0beaac98aa5a4ed37efc21eab3", "BgmVoice_MacroTick": "240ef618aa8c5e1615cc26f83ef96aecf3f2016d30bccee797c7a79b92392230", "Ngc_BgmNoiseNote": "9dd3f4d263c289d9fd849cad85f744382f2b09d417e472a269c51e6af3267393"},
}
UPDATES = ("level", "divider", "noise_note")


def _variant(key):
    factory, updates = map(int, key.split(":"))
    features = {name for bit, name in enumerate(("ecurves", "curves", "macros")) if factory >> bit & 1}
    if updates:
        features |= {"hold"} | {name for bit, name in enumerate(UPDATES) if (updates - 1) >> bit & 1}
    return {**_NATIVE, **_DIFFERS.get(key, {})}, frozenset(features)


VARIANTS = {f"{f}:{u}": _variant(f"{f}:{u}") for f in range(8) for u in range(9)}
# The stream opcode that selects each table's entry (not transcribed: refused when it is there).
SELECTS = {"ecurves": 0xFB, "curves": 0xFC, "macros": 0xFD}
# FE subcommand of each held-stream extension (music_hold.ABI).
SUBS = {"hold": 3, "level": 4, "divider": 5, "noise_note": 6, "routine": 7}
# EVENT_SET_MUSIC_ROUTINE (music_routine.attach): its branch in BgmVoice_Step and its function.
ROUTINE_BRANCH = "                } else if (sub == 7) {\n                    Ngc_BgmRoutine(*v->ptr++);\n"
ROUTINE = "6b17b914981bc14677eb94cff74b39456f901103c7009e1a4e3ed6a3259ef8cc"   # Ngc_BgmRoutine (runtime/music_hold.c)
ROUTINE_QUEUE = "static u8 ngc_routine_queue[4];\nstatic u8 ngc_routine_head, ngc_routine_tail;\n"
# EVENT_MUTE_CHANNEL (music_mute.attach): a muted voice held back in Bgm_Update like a voice an
# effect owns.
MUTE_DECL = "static unsigned char ngc_music_mute_mask;\n"


def _unmuted(update):
    """Bgm_Update without music_mute's edits (None when it has none)."""
    out = update
    for ch in range(4):
        held = f"(s_bgm_ch_used_by_sfx[{ch}] || (ngc_music_mute_mask & {1 << ch}u))"
        out = out.replace(f"!{held}", f"!s_bgm_ch_used_by_sfx[{ch}]")
    return None if out == update else out


STREAM = re.compile(r"^static const unsigned char (ngc_music_track\d+_ch\d)\[\] = \{(.*?)\};", re.M | re.S)
INSTRUMENTS = re.compile(r"^static const BgmInstrument s_bgm_instruments\[\] = \{\n(.*?)\n\};", re.M | re.S)
INSTRUMENT_FIELDS = ("attn env_on env_step env_speed env_curve_id pitch_curve_id vib_on vib_depth vib_speed "
                     "vib_delay sweep_on sweep_end sweep_step sweep_speed mode noise_config macro_id adsr_on "
                     "adsr_attack adsr_decay adsr_sustain adsr_release adsr_sustain_rate lfo_on lfo_wave lfo_rate "
                     "lfo_depth lfo_hold lfo2_on lfo2_wave lfo2_hold lfo2_rate lfo2_depth lfo_algo").split()
# Stream opcodes: argument bytes (BgmVoice_Step); None = turns an effect on (not transcribed).
OPCODE_ARGS = {0xF0: 1, 0xF1: None, 0xF2: None, 0xF3: None, 0xF4: 1, 0xF5: 1, 0xF6: 2, 0xF7: 1, 0xF8: 2,
               0xF9: None, 0xFA: None, 0xFB: 1, 0xFC: 1, 0xFD: 1}
FE_ARGS = {1: None, 2: None, 3: 1, 4: 1, 5: 2, 6: 1, 7: 1}   # a subcommand the variant lacks: one byte skipped
SHARED_C = {  # declaration in audio.c -> C type of the element and count (1 = scalar)
    "s_sfx_cmd": ("static PsgCmd s_sfx_cmd[4];", "PsgCmd", 4),
    "s_psg_shadow": ("static PsgCmd s_psg_shadow[4];", "PsgCmd", 4),
    "s_psg_pending": ("static PsgCmd s_psg_pending[4];", "PsgCmd", 4),
    "s_bgm_ch_used_by_sfx": ("static u8 s_bgm_ch_used_by_sfx[4];", "u8", 4),
    "s_bgm_restore_ch": ("static u8 s_bgm_restore_ch[4];", "u8", 4),
    "s_sfx_end_pending": ("static u8 s_sfx_end_pending[4];", "u8", 4),
    "s_attn_base": ("static u8 s_attn_base[4];", "u8", 4),
    "s_freq_base": ("static u8 s_freq_base[4];", "u8", 4),
    "s_sfx_active_mask": ("static u8 s_sfx_active_mask;", "u8", 1),
    "s_psg_pending_mask": ("static u8 s_psg_pending_mask;", "u8", 1),
    "s_psg_commit_mask": ("static u8 s_psg_commit_mask;", "u8", 1),
}
# The Z80's own state now: every statement still naming one is removed from the C.
Z80_OWNED = ("s_bgm_v0", "s_bgm_v1", "s_bgm_v2", "s_bgm_vn", "s_bgm_loop", "s_bgm_speed", "s_bgm_gate_percent",
             "s_bgm_fade_speed", "s_bgm_fade_counter", "s_bgm_fade_attn", "s_bgm_master_attn", "s_bgm_song_frame")


def functions(text):
    return {m[1]: m[0] for m in FUNCTION.finditer(text)}


def _instruments(text):
    m = INSTRUMENTS.search(text)
    if not m:
        return None
    rows = []
    for line in m[1].splitlines():
        values = [int(v) for v in re.findall(r"-?\d+", line)]
        if len(values) != len(INSTRUMENT_FIELDS):
            return None
        rows.append(dict(zip(INSTRUMENT_FIELDS, values)))
    return rows


def _effects(inst, curves, ecurves=None, macros=None):
    """The effects BgmVoice_UpdateFx runs for this instrument (music_z80_asm.EFFECTS). Its
    curves and macro only with their tables ({id: steps}, None without: ApplyInstrument sets 0);
    the envelope curve only with the envelope on (its only reader)."""
    out = set()
    if inst["adsr_on"]:
        out.add("adsr")
    if inst["env_on"]:
        out.add("env")
    if inst["sweep_on"] and inst["sweep_step"]:
        out.add("sweep")
    if inst["vib_on"] and inst["vib_depth"]:
        out.add("vib")
    if any(inst[p + "_on"] and inst[p + "_depth"] and inst[p + "_rate"] for p in ("lfo", "lfo2")):
        out.add("lfo")
    if curves and curves.get(inst["pitch_curve_id"]):
        out.add("pcurve")
    if ecurves and inst["env_on"] and ecurves.get(inst["env_curve_id"]):
        out.add("ecurve")
    if macros and macros.get(inst["macro_id"]):
        out.add("macro")
    return out


CURVE_TABLE = re.compile(r"^static const BgmPitchCurve s_bgm_pitch_curves\[\] = \{\n(.*?)\n\};", re.M | re.S)
CURVE_STEPS = re.compile(r"^static const signed short (s_bgm_pitch_curve\w+)\[\] = \{(.*?)\};", re.M | re.S)


def _curves(text, kind="pitch"):
    """{id: its steps} of s_bgm_pitch_curves / s_bgm_env_curves (an empty curve: []); of
    s_bgm_macros: {id: [(frames, attn_delta, pitch_delta)]}. None without the table."""
    head, steps_re = {
        "pitch": (CURVE_TABLE, CURVE_STEPS),
        "env": (re.compile(r"^static const BgmEnvCurve s_bgm_env_curves\[\] = \{\n(.*?)\n\};", re.M | re.S),
                re.compile(r"^static const signed char (s_bgm_env_curve\w+)\[\] = \{(.*?)\};", re.M | re.S)),
        "macro": (re.compile(r"^static const BgmMacro s_bgm_macros\[\] = \{\n(.*?)\n\};", re.M | re.S),
                  re.compile(r"^static const BgmMacroStep (s_bgm_macro\w+)\[\] = \{(.*?)\n\};", re.M | re.S)),
    }[kind]
    m = head.search(text)
    if not m:
        return None
    steps = {}
    for name, body in steps_re.findall(text):
        values = [int(v) for v in re.findall(r"-?\d+", body)]
        steps[name] = [tuple(values[i:i + 3]) for i in range(0, len(values), 3)] if kind == "macro" else values
    out = {}
    for k, entry in enumerate(re.findall(r"\{([^{}]*)\}", m[1])):
        name, count = (x.strip() for x in entry.split(","))
        out[k] = steps[name][:int(count)] if name != "0" else []
    return out


TRACKS = re.compile(r"\{ngc_music_track\d+_notes, \{([^}]*)\}, \{([^}]*)\}\}")


def ops(data, subs=frozenset({3, 4, 5}), refused=frozenset()):
    """[(offset, length, kind)] of a stream as BgmVoice_Step reads it; kind: "timed" (a note,
    FF or FE 03: the voice waits after it), "end" (00), "zero" (read in the same frame),
    "effect" (turns an effect on: not transcribed)."""
    out, i = [], 0
    while i < len(data):
        b = data[i]
        if b == 0x00:
            out.append((i, 1, "end"))
            i += 1
        elif b == 0xFF or b < 0xF0:
            out.append((i, 2, "timed"))
            i += 2
        elif b == 0xFE:
            sub = data[i + 1] if i + 1 < len(data) else None
            # FE 03..07 the driver lacks (subs: those it has) are unknown subcommands.
            n = FE_ARGS.get(sub, 1) if sub in subs or sub not in (3, 4, 5, 6, 7) else 1
            out.append((i, 2 + (n or 0), "effect" if n is None else "timed" if sub == 3 and sub in subs else "zero"))
            i += 2 + (n or 0)
        elif OPCODE_ARGS[b] is None or b in refused:
            out.append((i, 1, "effect"))
            i += 1
        else:
            out.append((i, 1 + OPCODE_ARGS[b], "zero"))
            i += 1 + OPCODE_ARGS[b]
    return out


def frame_bytes(data, loop, subs=frozenset({3, 4, 5}), refused=frozenset()):
    """The most ring bytes one voice reads in one frame: from any frame start (the stream start,
    after a timed opcode), zero-time opcodes then one timed opcode; through the end into the
    loop (the CPU copies the loop after the end); a loop point on the end itself ends the
    frame after a peek. None when a frame never ends (a loop without a timed opcode)."""
    table = ops(data, subs, refused)
    at = {o: k for k, (o, _, _) in enumerate(table)}
    starts = [0] + [o + n for o, n, kind in table if kind == "timed" and o + n < len(data)]
    best = 0
    for s in starts:
        k, total, wrapped = at[s], 0, False
        while True:
            o, n, kind = table[k]
            total += n
            if kind == "timed":
                break
            if kind == "end":
                if wrapped or loop not in at:
                    return None
                wrapped = True
                if data[loop] == 0x00:
                    total += 1          # the peek of the loop point
                    break
                k = at[loop]
                continue
            k += 1
            if k == len(table):
                return None
        best = max(best, total)
    return best


def streams(text):
    """{array name: (bytes, loop offset)} of every native song channel."""
    loops = {}
    for names, offsets in TRACKS.findall(text):
        for name, offset in zip(names.split(","), offsets.split(",")):
            name = name.strip()
            if name != "0":
                loops[name] = int(offset)
    out = {}
    for name, body in STREAM.findall(text):
        if name not in loops:
            raise CompileError(f"music on the Z80: {name} is in no track")
        out[name] = ([int(v) for v in re.findall(r"\d+", body)], loops[name])
    return out


def note_bytes(text, subs=frozenset({3, 4, 5})):
    """{note table array: 2 x the highest note (1..51) its songs' streams play}: the bytes of the
    table BgmNoteToDiv reads (a note above 51 is silence, never looked up)."""
    arrays = dict((n, d) for n, (d, _) in streams(text).items())
    out = {}
    for notes, names, _ in re.findall(r"\{(ngc_music_track\d+_notes), \{([^}]*)\}, \{([^}]*)\}\}", text):
        top = 0
        for name in (n.strip() for n in names.split(",")):
            data = arrays.get(name, [])
            for offset, _, kind in ops(data, subs):
                b = data[offset]
                if kind == "timed" and 0 < b <= 51:
                    top = max(top, b)
        out[notes] = max(out.get(notes, 0), 2 * top)
    return out


def eligible(text):
    """(True, (instruments, thresh, features, curves)) or (False, why)."""
    have = functions(text)
    extensions = set()
    # Set Music Routine / Mute Channel edit the driver: compared without their edits.
    if "BgmVoice_Step" in have and ROUTINE_BRANCH in have["BgmVoice_Step"]:
        if hashlib.sha256(have.get("Ngc_BgmRoutine", "").encode()).hexdigest() != ROUTINE or text.count(ROUTINE_QUEUE) != 1:
            return False, "Set Music Routine's driver code differs from the transcribed one"
        have["BgmVoice_Step"] = have["BgmVoice_Step"].replace(ROUTINE_BRANCH, "", 1)
        extensions.add("routine")
    if "Bgm_Update" in have and _unmuted(have["Bgm_Update"]) is not None:
        if text.count(MUTE_DECL) != 1:
            return False, "Mute Channel's driver code differs from the transcribed one"
        have["Bgm_Update"] = _unmuted(have["Bgm_Update"])
        extensions.add("mute")
    features = None
    for variant, (hashes, extra) in VARIANTS.items():
        if all(name in have and hashlib.sha256(have[name].encode()).hexdigest() == digest
               for name, digest in hashes.items()):
            features = set(extra) | extensions
            break
    if features is None:
        missing = [n for n in TRANSCRIBED if n not in have]
        return False, (f"driver function {missing[0]} absent" if missing and "Ngc_Bgm" not in missing[0] else
                       "driver functions differ from the transcribed variants (" + ", ".join(VARIANTS) + ")")
    subs = frozenset(SUBS[f] for f in features if f in SUBS)
    instruments = _instruments(text)
    if not instruments:
        return False, "instrument table not recognised"
    tables = {}
    for table, kind in (("curves", "pitch"), ("ecurves", "env"), ("macros", "macro")):
        if table in features:
            features.discard(table)
            tables[table] = _curves(text, kind)
            if tables[table] is None:
                return False, f"{kind} table not recognised"
    curves, ecurves, macros = (tables.get(k) for k in ("curves", "ecurves", "macros"))
    for inst in instruments:
        features |= _effects(inst, curves, ecurves, macros)
        if not -30000 <= inst["sweep_step"] <= 30000:
            return False, "a sweep step beyond +-30000"
    if "macro" in features:
        # MacroTick reads the macro of the voice's instrument without a bound (an instrument
        # changed during a macro): every id must be in the table.
        if any(i["macro_id"] >= len(macros) for i in instruments):
            return False, "a macro id beyond the macro table"
        if any(len(m) > 63 or any(not -100 <= a <= 100 for _, a, _ in m) for m in macros.values()):
            return False, "a macro beyond 63 steps or a volume step beyond +-100"
    if "ecurve" in features and any(not -100 <= v <= 100 for c in ecurves.values() for v in c):
        return False, "an envelope curve step beyond +-100"
    # The curves and macros the instruments use, in their order; their id becomes the index in
    # that list (0xff none: no table entry, an empty one, or the envelope off for its curve).
    keys = (("pitch_curve_id", "pcurve", curves), ("env_curve_id", "ecurve", ecurves), ("macro_id", "macro", macros))
    used = {field: sorted({i[field] for i in instruments if effect in _effects(i, curves, ecurves, macros)})
            for field, effect, _ in keys}
    rows = [{**{c: i[c] for c in INSTRUMENT_COLUMNS},
             **{field: used[field].index(i[field]) if i[field] in used[field] and effect in _effects(i, curves, ecurves, macros)
                else 0xFF for field, effect, _ in keys}}
            for i in instruments]
    refused = frozenset(op for name, op in SELECTS.items() if name in tables)
    thresh = 1
    for name, (data, loop) in streams(text).items():
        table = ops(data, subs, refused)
        if any(kind == "effect" for _, _, kind in table):
            return False, f"{name} turns an effect on"
        if [o for o, _, kind in table if kind == "end"] != [len(data) - 1] or table[-1][0] + table[-1][1] != len(data):
            return False, f"{name}: the end opcode is not its last byte alone"
        most = frame_bytes(data, loop, subs, refused)
        if most is None:
            return False, f"{name}: a frame without end (loop without a timed opcode)"
        thresh = max(thresh, most + 1)
    if layout(thresh) is None:
        return False, f"a frame reads {thresh - 1} stream bytes, more than half the largest ring"
    for line in re.findall(r"^\s*Bgm_SetGate\((\w+)\);", text, re.M):
        if line != "100":
            return False, "gate below 100 %"
    if re.search(r"\bs_bgm_master_attn\s*=(?!=)", text) and not re.search(
            r"\bs_bgm_master_attn\s*=\s*0", text):
        return False, "master volume changed"
    return True, (rows, thresh, frozenset(features),
                  {name: [table[k] for k in used[field]] for (field, _, table), name in zip(keys, ("curves", "ecurves", "macros"))
                   if table is not None})


def _addr(name):
    return 0x7000 + SHARED_LAYOUT[name][0]


PROTOTYPES = """static void ngc_z80_wait(void);
static void ngc_z80_refill(u8 budget);
static const u8 *ngc_z80_cur[4];
static const u8 *ngc_z80_end[4];
static const u8 *ngc_z80_loop[4];
static u8 ngc_z80_present;
static u8 ngc_z80_w[4];
static const u8 *ngc_z80_table;
"""


def refill_source(place):
    """ngc_z80_refill's body: per voice, the bytes the Z80 read since (its read index against
    the copy of the write index), copied with the ring's wrap and the stream's loop."""
    out = []
    RING_SIZE = place["ring_size"]
    voice, VOICE_SIZE = 0x7000 + place["voices"], place["vsize"]
    for v in range(4):
        ring = 0x7000 + place["ring"] + RING_SIZE * v
        out.append(f"""    if (ngc_z80_present & {1 << v}u) {{
        w = ngc_z80_w[{v}];
        n = (u8)((u8)(NGC_Z80B(0x{voice + V['rd'] + VOICE_SIZE * v:04X}u) - w - 1u) & {RING_SIZE - 1}u);
        if (n > budget) n = budget;
        if (n) {{
            s = ngc_z80_cur[{v}];
            d = (volatile u8 *)0x{ring:04X}u + w;
            ngc_z80_w[{v}] = (u8)((w + n) & {RING_SIZE - 1}u);
            do {{
                *d = *s++;
                if (s == ngc_z80_end[{v}]) s = ngc_z80_loop[{v}];
                if (++w == {RING_SIZE}u) {{ w = 0; d = (volatile u8 *)0x{ring:04X}u; }} else ++d;
            }} while (--n);
            ngc_z80_cur[{v}] = s;
            NGC_Z80B(0x{_addr('ring_w') + v:04X}u) = ngc_z80_w[{v}];
        }}
    }}
""")
    return "".join(out)


def cpu_source(names, thresh, tables, place):
    """The TLCS-900 side: waiting (and serving the Z80's refill requests), refilling the rings,
    the three posted routines. names: the song channel arrays (their end is the stream's);
    tables: {note table array: bytes its songs' notes read} (note_bytes)."""
    voice, VOICE_SIZE = 0x7000 + place["voices"], place["vsize"]
    RING_SIZE = place["ring_size"]
    # Ring top-ups: writes into the Z80's RAM are slow (~80 cycles a byte measured): a few
    # frames' worth at a start, then at most `step` a voice and a call (what was read, in
    # steady state); the Z80's check keeps it exact when a long pause read more.
    start, step = min(2 * thresh, RING_SIZE - 1), min(max(24, 2 * thresh), RING_SIZE - 1)
    table = ",\n    ".join(f"{{{n}, {n} + sizeof({n})}}" for n in names)
    notes = ",\n    ".join(f"{{{n}, {n} + {b}u}}" for n, b in sorted(tables.items())) or "{0, 0}"
    refill = refill_source(place)
    return f"""/* engine_speedups: the music runs on the Z80 (docs/PLAN_MUSIQUE_Z80.md, ngpcraft/music_z80.py). */
static const u8 * const ngc_z80_streams[][2] = {{
    {table}
}};
/* Each song's note table and the end of what its notes read of it (2 bytes a note). */
static const u8 * const ngc_z80_tables[][2] = {{
    {notes}
}};
/* Each voice's ring topped up after the Z80's read index, at most `budget` bytes: the stream,
 * then its loop point after its last byte (the end opcode, its only one: music_z80.eligible).
 * One block a voice with constant addresses, generated (refill_source): the generic loop over
 * pointer arrays cost ~900 cycles a voice and a call for a few bytes (measured). */
static void ngc_z80_refill(u8 budget)
{{
    u8 w;
    u8 n;
    const u8 *s;
    volatile u8 *d;
{refill}}}
/* Spun on in the TLCS-900's own RAM between two looks at the mailbox: every access to the
 * Z80's RAM takes the Z80's bus, a tight poll would stop the Z80 it waits for. */
static volatile u8 ngc_z80_idle;
/* The Z80 is done; frames it left for lack of stream bytes are finished first (the C did them
 * before anything that follows). Gives up after 2 frames (the Z80 needs far less) or, with the
 * VBlank interrupt off, after a fixed count: never hangs the game. */
static void ngc_z80_wait(void)
{{
    u8 start;
    u8 k;
    u16 guard;
    for (;;) {{
        start = g_vb_counter;
        guard = 0;
        while (NGC_Z80B(0x{_addr('mb_cmd'):04X}u)) {{
            for (k = 0; k < 16u; k++) ngc_z80_idle = k;
            if ((u8)(g_vb_counter - start) > 2u || ++guard == 0u) return;
        }}
        if (!NGC_Z80B(0x{_addr('z_need'):04X}u)) return;
        NGC_Z80B(0x{_addr('z_need'):04X}u) = 0;
        ngc_z80_refill(255u);
        NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = {MB_CONTINUE}u;   /* mb_arg: the frames left */
    }}
}}
static void Bgm_StartLoop4Ex(const u8 *stream0, u16 loop0, const u8 *stream1, u16 loop1, const u8 *stream2, u16 loop2, const u8 *streamN, u16 loopN)
{{
    const u8 *s[4];
    u16 l[4];
    u8 v;
    u8 i;
    ngc_z80_wait();
    s[0] = stream0; s[1] = stream1; s[2] = stream2; s[3] = streamN;
    l[0] = loop0; l[1] = loop1; l[2] = loop2; l[3] = loopN;
    ngc_z80_present = 0;
    for (v = 0; v < 4u; v++) {{
        ngc_z80_w[v] = 0;
        NGC_Z80B(0x{_addr('ring_w'):04X}u + v) = 0;
        NGC_Z80B(0x{voice + V['rd']:04X}u + (u16)v * {VOICE_SIZE}u) = 0;
        if (!s[v]) continue;
        for (i = 0; i < (u8)(sizeof(ngc_z80_streams) / sizeof(ngc_z80_streams[0])); i++) {{
            if (ngc_z80_streams[i][0] == s[v]) break;
        }}
        ngc_z80_present |= (u8)(1u << v);
        ngc_z80_cur[v] = s[v];
        ngc_z80_end[v] = ngc_z80_streams[i][1];
        ngc_z80_loop[v] = l[v] ? s[v] + l[v] : s[v];
    }}
    /* The note to divider conversion reads the song's table at its notes (entries 0..50 at
     * most): those bytes, and not again for the table already there. */
    if (s_bgm_note_table != ngc_z80_table) {{
        const u8 *t = s_bgm_note_table;
        const u8 *e = t + 102u;
        volatile u8 *d = (volatile u8 *)0x{_addr('note_table'):04X}u;
        for (i = 0; i < (u8)(sizeof(ngc_z80_tables) / sizeof(ngc_z80_tables[0])); i++) {{
            if (ngc_z80_tables[i][0] == t) {{ e = ngc_z80_tables[i][1]; break; }}
        }}
        while (t != e) *d++ = *t++;
        ngc_z80_table = s_bgm_note_table;
    }}
    ngc_z80_refill({start}u);
    NGC_Z80B(0x{_addr('start_mask'):04X}u) = ngc_z80_present;
    NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = {MB_START}u;
    s_bgm_last_vbl = g_vb_counter;
}}
/* Posted, not awaited: whatever touches the shared state next waits for the Z80 first, and the
 * Z80 serves its commands in order (awaiting it here cost a frame at a song change). */
static void Bgm_Stop(void)
{{
    ngc_z80_wait();
    ngc_z80_present = 0;
    NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = {MB_STOP}u;
    s_bgm_last_vbl = g_vb_counter;
}}
/* The idle test (14 bytes of the Z80's RAM) is the Z80's: it keeps an idle call's frames for
 * the next busy one, as the C's s_bgm_last_vbl did (music_z80_asm do_step). */
static void Bgm_Update(void)
{{
    u8 elapsed;
    elapsed = (u8)(g_vb_counter - s_bgm_last_vbl);
    if (elapsed == 0) {{
        return;
    }}
    s_bgm_last_vbl = g_vb_counter;
    if (ngc_z80_present) ngc_z80_refill({step}u);
    NGC_Z80B(0x{_addr('mb_arg'):04X}u) = elapsed;
    NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = {MB_STEP}u;
}}
"""


def _prototype(name):
    return re.compile(r"^static [^;{]*\b" + name + r"\s*\([^;{]*\);\n", re.M)


VARIABLE = re.compile(r"^static (?:const )?[\w ]+?[\s*]+(\w+)(?:\[[^\]]*\])*(?: = (?:\{.*?\}|[^;]*))?;\n",
                      re.M | re.S)


def _named_elsewhere(text):
    """Static functions and variables named outside their own definition."""
    named = set()
    for name, body in functions(text).items():
        rest = _prototype(name).sub("", text.replace(body, ""))
        if re.search(r"\b" + name + r"\b", rest):
            named.add(name)
    for m in VARIABLE.finditer(text):
        if "(" in m[0].split("=")[0]:
            continue
        if len(re.findall(r"\b" + m[1] + r"\b", text)) > len(re.findall(r"\b" + m[1] + r"\b", m[0])):
            named.add(m[1])
    return named


def _remove_unreferenced(text, candidates):
    """Remove the static functions (and prototypes) and variables of `candidates` no longer named:
    only what the change itself left unused (a function named later by another module, such
    as Sfx_Stop by save_emit, is never a candidate)."""
    while True:
        removed = False
        for name, body in functions(text).items():
            if name not in candidates:
                continue
            rest = _prototype(name).sub("", text.replace(body, ""))
            if not re.search(r"\b" + name + r"\b", rest):
                text = rest
                removed = True
        for m in list(VARIABLE.finditer(text)):
            name = m[1]
            if name not in candidates or "(" in m[0].split("=")[0]:
                continue
            if len(re.findall(r"\b" + name + r"\b", text)) == len(re.findall(r"\b" + name + r"\b", m[0])):
                text = text.replace(m[0], "", 1)
                removed = True
                break
        if not removed:
            return text


def fast_music(text):
    """(audio.c, report)."""
    ok, detail = eligible(text)
    named_before = _named_elsewhere(text) if ok else set()
    if not ok:
        return text, {"music_z80": False, "why": detail}
    inst_attn, thresh, features, tables = detail
    try:
        code, symbols = assemble(source(inst_attn, thresh, features, tables.get("curves", ()), tables.get("ecurves", ()),
                                        tables.get("macros", ())))
    except (ValueError, CompileError) as e:      # beyond what the player holds: the C driver
        return text, {"music_z80": False, "why": f"Z80 program not built: {e}"}
    place = layout(thresh, features)
    if len(code) > place["code_end"]:
        return text, {"music_z80": False, "why": "Z80 program larger than its space"}
    have = functions(text)
    # Shared variables in the Z80's RAM.
    for name, (decl, ctype, count) in SHARED_C.items():
        if text.count(decl) != 1:
            raise CompileError(f"music on the Z80: declaration {decl!r} changed")
        address = _addr(name)
        if count == 1:
            define = f"#define {name} (*({ctype} *)0x{address:04X})"
        else:
            define = f"#define {name} (*({ctype} (*)[{count}])0x{address:04X})"
        text = text.replace(decl, define + "   /* Z80 RAM, shared (music_z80.py) */")
    # Mute Channel's mask and Set Music Routine's queue in the Z80's RAM (it writes them); their
    # readers on the TLCS-900 wait for the Z80 first (the C ran Bgm_Update before them).
    if "mute" in features:
        text = text.replace(MUTE_DECL, f"#define ngc_music_mute_mask (*(volatile u8 *)0x{_addr('ngc_music_mute_mask'):04X})\n")
        anchor = "void ngc_music_mute(unsigned char mask)\n{\n    u8 ch;\n    u8 bit;\n    PsgCmd silence;\n"
        if text.count(anchor) != 1:
            raise CompileError("music on the Z80: Mute Channel anchor changed")
        text = text.replace(anchor, anchor + "    ngc_z80_wait();\n")
    if "routine" in features:
        text = text.replace(ROUTINE_QUEUE,
                            f"#define ngc_routine_queue (*(volatile u8 (*)[4])0x{_addr('ngc_routine_queue'):04X})\n"
                            f"#define ngc_routine_head (*(volatile u8 *)0x{_addr('ngc_routine_head'):04X})\n"
                            f"#define ngc_routine_tail (*(volatile u8 *)0x{_addr('ngc_routine_tail'):04X})\n")
        for anchor in ("unsigned char ngc_music_routine_poll(unsigned char * data)\n{\n",
                       "void ngc_music_routine_clear(void)\n{\n"):
            if text.count(anchor) != 1:
                raise CompileError("music on the Z80: Set Music Routine anchor changed")
            text = text.replace(anchor, anchor + "    ngc_z80_wait();\n")
        # At power-on the Z80's program is not there yet (its mailbox is random RAM): the queue
        # is emptied directly.
        init = "void ngc_audio_init(void)\n{\n    ngc_music_routine_clear();\n"
        if text.count(init) != 1:
            raise CompileError("music on the Z80: Set Music Routine init anchor changed")
        text = text.replace(init, "void ngc_audio_init(void)\n{\n    ngc_routine_head = 0u;\n    ngc_routine_tail = 0u;\n")
    # The Z80 program in place of the relay.
    m = re.search(r"static const u8 s_z80drv\[\] = \{\n.*?\n\};", text, re.S)
    if not m:
        raise CompileError("music on the Z80: relay program changed")
    rows = [", ".join(f"0x{b:02X}" for b in code[i:i + 16]) for i in range(0, len(code), 16)]
    text = text.replace(m[0], "static const u8 s_z80drv[] = {\n    " + ",\n    ".join(rows) + "\n};\n"
                        "#define NGC_Z80B(a) (*(volatile u8 *)(a))\n" + PROTOTYPES)
    # The program copy by pointers (its indexed loop took a whole frame for 2 KB: a frame lost
    # at power-on); same bytes, same order.
    copy = ("    ram = (u8 *)0x7000;\n    for (i = 0; i < sizeof(s_z80drv); i++) {\n"
            "        ram[i] = s_z80drv[i];\n    }\n")
    if text.count(copy) != 1:
        raise CompileError("music on the Z80: Sounds_Init program copy changed")
    text = text.replace(copy, "    {\n        const u8 *src = s_z80drv;\n"
                              "        ram = (u8 *)0x7000;\n"
                              "        for (i = sizeof(s_z80drv); i; i--) *ram++ = *src++;\n    }\n")
    start = "    (*(volatile u16 *)0x00B8) = 0x5555;\n    Sounds_ResetState();"
    if text.count(start) != 1:
        raise CompileError("music on the Z80: Sounds_Init changed")
    # The shared bytes the Z80 does not set itself: the console's RAM starts random (a random
    # z_need would post frames that were never asked for).
    text = text.replace(start, f"    NGC_Z80B(0x{_addr('mb_cmd'):04X}u) = 0;\n"
                               f"    NGC_Z80B(0x{_addr('enabled_mask'):04X}u) = 0;\n"
                               f"    NGC_Z80B(0x{_addr('z_need'):04X}u) = 0;\n"
                               "    ngc_z80_present = 0;\n    ngc_z80_table = 0;\n" + start)
    # The posted routines replace the C ones: after the song arrays (the stream table names
    # them), before the audio service; prototypes after the relay array.
    for name in ("Bgm_StartLoop4Ex", "Bgm_Stop", "Bgm_Update"):
        text = text.replace(have[name], "")
    anchor = "void ngc_audio_service(void)"
    if text.count(anchor) != 1:
        raise CompileError("music on the Z80: audio service anchor changed")
    subs = frozenset(SUBS[f] for f in features if f in SUBS)
    text = text.replace(anchor, cpu_source(list(streams(text)), thresh, note_bytes(text, subs), place)
                        + anchor)
    # The TLCS-900 waits for the Z80 before touching the shared state.
    for anchor, add in (("    ngc_audio_last_frame = g_vb_counter;\n    /* Keep the proven V3 order",
                         "    ngc_audio_last_frame = g_vb_counter;\n    ngc_z80_wait();\n    /* Keep the proven V3 order"),
                        ("static void Sfx_Stop(void)\n{\n    u8 i;\n", "static void Sfx_Stop(void)\n{\n    u8 i;\n    ngc_z80_wait();\n")):
        if anchor in text:
            if text.count(anchor) != 1:
                raise CompileError("music on the Z80: wait anchor changed")
            text = text.replace(anchor, add)
    if "ngc_z80_wait();\n    /* Keep the proven V3 order" not in text:
        raise CompileError("music on the Z80: audio service anchor changed")
    # Statements on the Z80's own state go; the closure removes what only they used.
    owned = re.compile(r"^[ \t]+[^\n]*\b(?:" + "|".join(Z80_OWNED) + r")\b[^\n]*;\n", re.M)
    text = owned.sub("", text)
    text = _remove_unreferenced(text, named_before | set(Z80_OWNED))
    for name in Z80_OWNED:
        if re.search(rf"\b{name}\b", text):
            raise CompileError(f"music on the Z80: {name} still named in audio.c")
    return text, {"music_z80": True, "z80_bytes": len(code), "instruments": len(inst_attn), "frame_bytes": thresh - 1,
                  "features": sorted(features)}
