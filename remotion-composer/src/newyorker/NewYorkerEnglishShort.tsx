import React, {useMemo} from 'react';
import {
  AbsoluteFill,
  Audio,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {BoilingFrame} from './components/BoilingFrame';
import {GlossaryScreenshotCard} from './components/GlossaryScreenshotCard';
import {KaraokeSubtitles} from './components/KaraokeSubtitles';
import {PaperCanvas} from './components/PaperCanvas';
import type {Episode} from './episode';
import {PAPER} from './theme';

const Body: React.FC<{episode: Episode}> = ({episode}: {episode: Episode}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const seconds = frame / fps;

  let sceneIndex = 0;
  for (let index = 0; index < episode.scenes.length; index++) {
    if (seconds >= episode.scenes[index].startSec) {
      sceneIndex = index;
    }
  }
  const scene = episode.scenes[sceneIndex];
  const previousScene =
    sceneIndex > 0 ? episode.scenes[sceneIndex - 1] : null;
  const sceneStartFrame = Math.round(scene.startSec * fps);
  const localStartFrame = frame - sceneStartFrame;

  return (
    <>
      <BoilingFrame
        episode={episode}
        scene={scene}
        previousScene={previousScene}
        sceneIndex={sceneIndex + 1}
        fromFrame={Math.round(scene.startSec * fps)}
        durationInFrames={Math.max(
          fps,
          Math.round((scene.endSec - scene.startSec) * fps),
        )}
      />
      <KaraokeSubtitles episode={episode} frame={frame} />
      <Audio src={staticFile(episode.mainAudio)} />
      {sceneIndex > 0 && localStartFrame < 15 ? (
        <Audio src={staticFile('audio/page_flip.mp3')} volume={0.6} />
      ) : null}
    </>
  );
};

export const NewYorkerEnglishShort: React.FC<{episode: Episode}> = ({
  episode,
}: {
  episode: Episode;
}) => {
  const {fps, durationInFrames} = useVideoConfig();
  const audioFrames = Math.round(episode.audioDurationSec * fps);
  const endingFrames = Math.max(1, durationInFrames - audioFrames);

  return (
    <AbsoluteFill style={{backgroundColor: PAPER}}>
      <PaperCanvas>
        <Sequence durationInFrames={audioFrames}>
          <Body episode={episode} />
        </Sequence>
        <Sequence from={audioFrames} durationInFrames={endingFrames}>
          <CardTurn episode={episode} />
          <Audio src={staticFile('audio/page_flip.mp3')} volume={0.6} />
        </Sequence>
        <BodyAudio episode={episode} />
      </PaperCanvas>
    </AbsoluteFill>
  );
};

// The glossary card slides in from the right like a turned page (0.45s),
// with a page-flip sound.
const CardTurn: React.FC<{episode: Episode}> = ({
  episode,
}: {
  episode: Episode;
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const turnFrames = Math.max(1, Math.round(fps * 0.45));
  const x = interpolate(frame, [0, turnFrames], [1080, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        transform: `translateX(${x}px)`,
      }}
    >
      <GlossaryScreenshotCard episode={episode} />
    </div>
  );
};

const BodyAudio: React.FC<{episode: Episode}> = ({
  episode,
}: {
  episode: Episode;
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const seconds = frame / fps;

  // Comic bed ducked under speech (0.4 bed / 0.18 duck).
  const speechWindows = useMemo(() => {
    const windows: Array<[number, number]> = [];
    for (const s of episode.sentences) {
      for (const w of s.words) {
        windows.push([w.start - 0.08, w.end + 0.15]);
      }
    }
    return windows;
  }, [episode]);
  const speaking = speechWindows.some(
    ([a, b]: [number, number]) => seconds >= a && seconds <= b,
  );
  const bgmVolume = speaking ? 0.18 : 0.4;

  return (
    <>
      <Audio src={staticFile(episode.mainAudio)} />
      <Audio src={staticFile('audio/bgm_comic.mp3')} volume={bgmVolume} />
    </>
  );
};
