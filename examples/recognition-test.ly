\version "2.24.3"
\header { title = "Recognition test" composer = "Jianpu Studio test fixture" tagline = ##f }
\paper { indent = 0\mm }
\relative c' {
  \clef treble \key c \major \time 4/4 \tempo 4 = 96
  c4 c g' g | a a g2 | f4 f e e | d d c2 \break
  g'4 g f f | e e d2 | g4 g f f | e e d2 \break
  c4 c g' g | a a g2 | f4 f e e | d d c2 \bar "|."
}
