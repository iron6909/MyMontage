export type Word = {
  word: string;
  startSec: number;
  endSec: number;
};

// Source JSON uses short keys for words; normalized in this type.
export type RawWord = {
  word: string;
  start: number;
  end: number;
};

export type Sentence = {
  id: number;
  sceneId: string;
  startSec: number;
  endSec: number;
  text: string;
  textCn: string;
  speaker?: string;
  words: RawWord[];
};

export type Scene = {
  id: string;
  label: string;
  startSec: number;
  endSec: number;
  cameraMotion: string;
  transition: string;
  visualAction: string;
  prompt: string;
};

export type GlossaryItem = {
  phrase: string;
  phonetic: string;
  tag: string;
  meaning: string;
  example: string;
};

export type Episode = {
  epId: string;
  status: string;
  episode: string;
  level: string;
  title: string;
  titleCn: string;
  hookDurationSec: number;
  endingCardDurationSec: number;
  mainAudio: string;
  showCnSubtitle: boolean;
  audioDurationSec: number;
  characterAnchor: string;
  scenes: Scene[];
  sentences: Sentence[];
  glossaryCard: GlossaryItem[];
};
