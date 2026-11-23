const DIAGRAM = {
  label: 'Animated diagram: a message written in the MAX app reaches the MAX Bot API, the plugin\'s transport (long polling or a webhook) saves its place and passes it on; inbound checks the sender and fetches media; Hermes thinks while a typing indicator shows, voice notes go to a speech-to-text provider, and the answer streams back through outbound formatting and a rate limiter.',
  idle: 'WAITING FOR MESSAGES',
  stepWord: 'STEP',
  stepsAria: 'Diagram steps',
  stepSeconds: 7,
  holdSeconds: 14,
  switcher: { scheme: 'Diagram', aria: 'Diagram view' },

  nodes: {
    user: { title: 'MAX app', sub: 'you, on a phone', icon: 'phone', group: 'client' },
    maxapi: { title: 'MAX Bot API', sub: 'platform-api2.max.ru', icon: 'cloud', group: 'ext' },
    transport: { title: 'Transport', sub: 'long poll · webhook', icon: 'queue', group: 'edge' },
    state: { title: 'State', sub: 'marker · dedup', icon: 'db', group: 'data' },
    inbound: { title: 'Inbound', sub: 'sender · media', icon: 'shield', group: 'edge' },
    hermes: { title: 'Hermes Agent', icon: 'bot', group: 'core', always: true, screen: true,
              chips: ['pair', 'think', 'reply'], status: 'waiting for you' },
    stt: { title: 'Speech-to-text', sub: 'SpeechKit · Salute', icon: 'chat', group: 'ext', from: 5 },
    outbound: { title: 'Outbound', sub: 'HTML · uploads', icon: 'send', group: 'core', from: 1 },
    limiter: { title: 'Rate limiter', sub: '30/s · 2/s per chat', icon: 'bolt', group: 'data', from: 1 },
  },

  edges: {
    'in-user': { num: true, arrows: 'both' },
    'to-transport': {},
    'to-state': { dashed: true, arrows: 'both' },
    'to-inbound': {},
    'to-hermes': { from: 1, num: true },
    'to-outbound': { from: 1 },
    'to-limiter': { from: 1, num: true },
    'to-max': { from: 1 },
    'to-stt': { from: 5, num: true, arrows: 'both' },
  },

  layouts: {
    wide: {
      w: 960, h: 520,
      nodes: {
        user: { x: 24, y: 40, w: 170, h: 60 },
        maxapi: { x: 24, y: 210, w: 170, h: 100 },
        state: { x: 242, y: 24, w: 170, h: 56 },
        transport: { x: 242, y: 120, w: 170, h: 60 },
        inbound: { x: 460, y: 120, w: 170, h: 60 },
        hermes: { x: 690, y: 80, w: 246, h: 300 },
        outbound: { x: 460, y: 340, w: 170, h: 60 },
        limiter: { x: 242, y: 340, w: 170, h: 60 },
        stt: { x: 690, y: 436, w: 246, h: 60 },
      },
      edges: {
        'in-user': [[109, 100], [109, 210]],
        'to-transport': [[194, 240], [218, 240], [218, 150], [242, 150]],
        'to-state': [[327, 120], [327, 80]],
        'to-inbound': [[412, 150], [460, 150]],
        'to-hermes': [[630, 150], [690, 150]],
        'to-outbound': [[690, 370], [630, 370]],
        'to-limiter': [[460, 370], [412, 370]],
        'to-max': [[242, 370], [218, 370], [218, 280], [194, 280]],
        'to-stt': [[813, 380], [813, 436]],
      },
    },
    tall: {
      w: 420, h: 700, numbers: false, packet: 5,
      nodes: {
        user: { x: 10, y: 10, w: 180, h: 52 },
        maxapi: { x: 220, y: 10, w: 180, h: 52 },
        state: { x: 6, y: 96, w: 92, h: 52 },
        transport: { x: 120, y: 96, w: 180, h: 52 },
        inbound: { x: 120, y: 182, w: 180, h: 52 },
        hermes: { x: 16, y: 268, w: 380, h: 230 },
        stt: { x: 10, y: 530, w: 180, h: 52 },
        outbound: { x: 220, y: 530, w: 180, h: 52 },
        limiter: { x: 220, y: 616, w: 180, h: 52 },
      },
      edges: {
        'in-user': [[190, 36], [220, 36]],
        'to-transport': [[310, 62], [310, 79], [210, 79], [210, 96]],
        'to-state': [[120, 122], [98, 122]],
        'to-inbound': [[210, 148], [210, 182]],
        'to-hermes': [[210, 234], [210, 268]],
        'to-stt': [[100, 498], [100, 530]],
        'to-outbound': [[310, 498], [310, 530]],
        'to-limiter': [[310, 582], [310, 616]],
        'to-max': [[400, 642], [412, 642], [412, 36], [400, 36]],
      },
    },
  },

  steps: [
    {
      title: 'A message arrives',
      text: 'MAX hands the message to the plugin by long polling (or a webhook). Its place is saved first, so a restart never reads it twice or loses one.',
      run(f) {
        if (f.flow('in-user', 0.2, 1.1) || f.at(1.1)) f.lit('user', 'in-user');
        if (f.flow('to-transport', 1.2, 2.1) || f.at(2.1)) f.lit('maxapi', 'to-transport', 'transport');
        if (f.flow('to-state', 2.2, 2.9) || f.at(2.9)) f.lit('to-state', 'state');
        if (f.flow('to-inbound', 3.0, 3.8) || f.at(3.8)) f.lit('to-inbound', 'inbound');
        f.screen('LONG POLL', f.t < 2.9 ? '1 update · chat 4242' : 'marker 1042 saved · 1 update');
      },
    },
    {
      title: 'A stranger gets a code',
      text: 'Hermes decides who may talk to it. A new person gets a pairing code for the owner to approve, and the server downloads nothing they sent.',
      run(f) {
        f.chip(0);
        f.lit('inbound');
        if (f.flow('to-hermes', 0.2, 1.0) || f.at(1.0)) f.lit('to-hermes');
        f.flow('to-outbound', 1.8, 2.5);
        f.flow('to-limiter', 2.5, 3.1);
        f.flow('to-max', 3.1, 3.9);
        f.flow('in-user', 3.9, 4.7, { reverse: true });
        if (f.at(2.5)) f.lit('outbound', 'to-outbound');
        if (f.at(3.1)) f.lit('limiter', 'to-limiter');
        if (f.at(3.9)) f.lit('to-max', 'maxapi');
        if (f.at(4.7)) f.lit('user', 'in-user');
        f.screen('NEW SENDER', f.t < 1.6 ? 'id 4815 · not on the list' : 'pairing code K7Q2-9FX4 sent');
      },
    },
    {
      title: 'Typing while it thinks',
      text: 'While the agent works, the plugin shows "typing…" in MAX every two seconds — Hermes keeps it alive and pauses it while you decide something.',
      run(f) {
        f.chip(1);
        for (let k = 0; k < 3; k++) {
          const a = 0.3 + k * 1.6;
          f.flow('to-max', a, a + 0.7);
          f.flow('in-user', a + 0.7, a + 1.3, { reverse: true });
        }
        f.lit('to-outbound', 'to-limiter', 'outbound', 'limiter');
        if (f.at(0.3)) f.lit('maxapi', 'to-max');
        if (f.at(1.0)) f.lit('user', 'in-user');
        f.screen('THINKING', 'typing_on → chat 4242 · every 2 s');
      },
    },
    {
      title: 'The answer streams in',
      text: 'The reply appears and grows by edits. Markdown becomes MAX HTML, long text splits at 3 500 characters, and the limiter keeps MAX\'s pace.',
      run(f) {
        f.chip(2);
        for (let k = 0; k < 3; k++) {
          const a = 0.2 + k * 1.3;
          f.flow('to-outbound', a, a + 0.5);
          f.flow('to-limiter', a + 0.5, a + 0.9);
          f.flow('to-max', a + 0.9, a + 1.5);
        }
        if (f.at(0.2)) f.lit('to-outbound', 'outbound', 'to-limiter', 'limiter', 'to-max', 'maxapi');
        if (f.flow('in-user', 4.4, 5.1, { reverse: true }) || f.at(5.1)) f.lit('user', 'in-user');
        f.screen('STREAMING', f.t < 4 ? `edit ${1 + Math.min(2, Math.floor(f.t / 1.3))} · ≤ 2 edits/s` : '1 812 chars · 1 message · done');
      },
    },
    {
      title: 'You approve the risky part',
      text: 'Before a dangerous command Hermes asks with buttons. Only the person whose turn it is can press them; anyone else is refused.',
      run(f) {
        f.lit('outbound', 'limiter', 'maxapi');
        f.flow('to-max', 0.3, 1.0);
        f.flow('in-user', 1.0, 1.6, { reverse: true });
        f.status(f.t < 2.4 ? f.ease((f.t - 0.6) / 0.5) : 1 - f.ease((f.t - 4.6) / 0.4));
        if (f.flow('in-user', 2.4, 3.1, { warm: true }) || f.at(1.0)) f.lit('user', 'in-user');
        f.flow('to-transport', 3.1, 3.7, { warm: true });
        f.flow('to-inbound', 3.7, 4.2, { warm: true });
        f.flow('to-hermes', 4.2, 4.7, { warm: true });
        if (f.at(3.1)) f.lit('transport', 'to-transport');
        if (f.at(3.7)) f.lit('inbound', 'to-inbound');
        if (f.at(4.2)) f.lit('to-hermes');
        f.screen(f.t < 4.7 ? 'APPROVE?' : 'APPROVED', f.t < 4.7 ? 'git clean -fdx · once / deny' : 'git clean -fdx · once · owner');
      },
    },
    {
      title: 'Voice notes become text',
      text: 'A voice note goes to the speech-to-text provider you chose: Yandex SpeechKit, Sber SaluteSpeech, or a local Whisper.',
      run(f) {
        f.flow('in-user', 0.2, 0.9);
        f.flow('to-transport', 0.9, 1.4);
        f.flow('to-inbound', 1.4, 1.8);
        f.flow('to-hermes', 1.8, 2.3);
        f.lit('user', 'transport', 'inbound', 'to-hermes');
        if (f.at(2.3)) f.lit('stt', 'to-stt');
        f.flow('to-stt', 2.4, 3.1);
        f.flow('to-stt', 3.5, 4.2, { reverse: true });
        f.screen(f.t < 4.2 ? 'VOICE 0:23' : 'TRANSCRIBED', f.t < 4.2 ? 'ogg/opus → yandex_speechkit' : '"move the call to Friday"');
      },
    },
  ],
};
