# Practice List Feature Implementation

## Overview
Added a fully functional pronunciation practice tray to the practice list view, allowing users to stay in the practice list while practicing word pronunciation with recording, feedback, and scoring.

## Problem Statement
Previously, the practice list view had a "Practice" button that would only play audio when clicked. The button styling was also secondary (outline style) rather than primary, making it less prominent. Users couldn't access the full pronunciation practice experience (recording, feedback, scoring) without leaving the practice list view.

## Solution
Recreated the entire pronunciation practice tray (backdrop + controls) inside the practice view so users can:
- Click "Practice" on any word in the practice list
- See a tray slide in from the right with the word displayed
- Play the word audio automatically (replay button available)
- Record their pronunciation using the mic button
- Get instant feedback on accuracy with word-by-word comparison
- Remain in the practice list view to continue practicing other words

## Technical Implementation

### 1. Button Styling Update
**File**: `static/index.html` (lines 901-913)

Changed `.practice-btn` from outline style to primary:
```css
.practice-btn {
  background: var(--k-text-primary);  /* #1A1A1A - solid black */
  border: 1px solid var(--k-text-primary);
  color: white;
  ...
}
.practice-btn:hover { 
  background: var(--k-teal-dark);     /* Changes to teal on hover */
  border-color: var(--k-teal-dark);
  color: white; 
}
```

### 2. Added Backdrop & Tray to Practice View
**File**: `static/index.html` (lines 2217-2250)

Added two new elements inside `#practice-view`:
- **Backdrop**: `practice-drill-backdrop` - darkens background when tray is open
- **Tray**: `practice-drill-tray` - slides in from right side with:
  - Header with "Word Drill" title and close button
  - Word display area
  - Replay button (with play icon)
  - Mic button (with microphone icon)
  - Live transcript display (shows text as user speaks)
  - Score display (percentage + bar + result label)
  - Phrase diff visualization (shows which words were correct/wrong/missing)
  - Feedback items (pronunciation tips and corrections)

**Key IDs created**:
- `practice-drill-backdrop` - backdrop overlay
- `practice-drill-tray` - tray container
- `practice-psdp-sentence-text` - word display
- `practice-para-drill-replay-btn` - replay button
- `practice-para-drill-mic-btn` - mic button
- `practice-psdp-live-transcript` - live transcript container
- `practice-psdp-live-transcript-text` - live transcript text
- `practice-psdp-score-area` - score display container
- `practice-psdp-score-pct` - score percentage
- `practice-psdp-score-bar-fill` - score bar fill
- `practice-psdp-result-label` - pass/fail label
- `practice-psdp-phrase-diff` - word-by-word comparison
- `practice-psdp-feedback-items` - feedback list

### 3. Updated JavaScript Functions

#### A. `enterWordDrill(word, tip)` (lines 4515-4567)
Modified to detect current view and use appropriate tray:
```javascript
const inPracticeView = currentView === 'practice';
const trayId = inPracticeView ? 'practice-drill-tray' : 'para-sent-drill-tray';
// ... dynamically set all element IDs based on view ...
```

#### B. Created Practice-Specific Drill Functions (lines 4780-4814)
- **`exitPracticeDrill()`** - closes tray, stops mic, resets state
- **`replayPracticeDrillAudio()`** - plays word audio again
- **`togglePracticeDrillMic()`** - toggles mic recording with visual feedback

#### C. Updated `renderWordDrillFeedback(data, userText)` (lines 4626-4712)
Now detects view and renders feedback in correct tray:
- Determines which element IDs to use based on `currentView`
- Renders score percentage and bar
- Renders word-by-word diff (correct/wrong/missing)
- Renders feedback items (pronunciation tips, grammar notes)
- Re-enables mic button for retry

#### D. Updated Speech Recognition Handlers
Three handler functions were updated to support practice view:

**`recognition.onresult`** (lines 2914-2945)
- Added case for `currentView === 'practice'`
- Updates `practice-psdp-live-transcript-text` with live transcription
- Submits transcript after 2 seconds of silence

**`recognition.onerror`** (lines 2961-2984)
- Removes listening class from `practice-para-drill-mic-btn`
- Hides transcript on error

**`recognition.onend`** (lines 2985-3007)
- Removes listening class from `practice-para-drill-mic-btn`
- Handles cleanup when mic recording ends

#### E. Updated `submitTranscript()` (lines 2867-2897)
Added case for `view === 'practice'`:
```javascript
else if (view === 'practice') {
  document.getElementById('practice-para-drill-mic-btn').classList.remove('listening');
  handleDrillResult(text, confidence);
}
```
Calls `handleDrillResult()` which:
1. Calls `handleWordDrillResult()` if it's a word drill
2. Makes API call to `/paragraph/analyze` endpoint
3. Calls `renderWordDrillFeedback()` with results

### 4. Added Position:Relative to Practice View
**File**: `static/index.html` (line 2209)

Updated practice view to support absolute positioning of tray:
```html
<div id="practice-view" style="display:none; flex:1; flex-direction:column; overflow:hidden; position:relative;">
```

## Data Flow

```
User clicks "Practice" button
    ↓
enterWordDrill(word, tip) called
    ↓
Detect currentView === 'practice'
    ↓
Get/create correct element IDs (practice-*)
    ↓
Reset tray content
    ↓
Open tray + backdrop (add 'open' class)
    ↓
Fetch TTS audio via /tts endpoint
    ↓
Enable replay & mic buttons
    ↓
Play audio automatically
    ↓
User clicks mic button
    ↓
togglePracticeDrillMic() starts recognition
    ↓
User speaks word
    ↓
recognition.onresult fires
    ↓
Update live-transcript-text in real-time
    ↓
After 2 seconds silence, submitTranscript() called
    ↓
handleDrillResult(text, confidence)
    ↓
handleWordDrillResult() makes API call to /paragraph/analyze
    ↓
renderWordDrillFeedback() renders results in practice view
    ↓
User sees score, word comparison, and feedback tips
```

## Existing Functions Utilized

- **`handleDrillResult(text, confidence)`** - Routes to word/sentence drill result handlers
- **`handleWordDrillResult(text, confidence)`** - Makes API call and processes response
- **`playAudio(url)`** - Plays TTS audio
- **`esc(text)`** - Escapes HTML special characters for safety
- **`switchView(view)`** - Switches between different UI views

## CSS Reuse

The practice tray uses existing CSS classes:
- `.psdp-header`, `.psdp-title`, `.psdp-close-btn` - header styling
- `.psdp-body` - body container
- `.psdp-sentence` - word display
- `.psdp-controls` - button container
- `.para-replay-btn`, `.para-mic-btn` - button styling
- `.psdp-live-transcript` - transcript display
- `.psdp-score-*` - score styling
- `.diff-word`, `.feedback-item` - feedback styling

No new CSS classes were created; all styling reuses existing design system tokens.

## Testing Checklist

- [ ] Practice button displays with primary (solid black) styling
- [ ] Clicking Practice opens tray from the right side
- [ ] Word displays in tray
- [ ] Audio plays automatically
- [ ] Replay button plays audio again
- [ ] Mic button enables recording
- [ ] Live transcript updates during speech
- [ ] Transcript submits after 2 seconds silence
- [ ] Score displays correctly
- [ ] Word comparison shows correct/wrong/missing
- [ ] Feedback items display
- [ ] Close button closes tray
- [ ] Can practice multiple words without leaving practice list view
- [ ] All element IDs are found (no console errors)
- [ ] Works in Chrome/Edge (requires Web Speech API)

## Browser Support

Requires **Chrome or Edge** - the Web Speech API is only available in these browsers. Other browsers will show disabled state on mic button.

## Future Enhancements

- [ ] Confidence threshold warning (currently planned, see CLAUDE.md "Pending features")
- [ ] Ability to skip to next word without closing tray
- [ ] Mark word as "mastered" to exclude from future practice
- [ ] Statistics tracking (how many times practiced, best score, etc.)
- [ ] Export practice results

## Files Modified

- `static/index.html` - All changes in this single file

## Related Code Sections

- **Paragraph view tray**: Lines 2333-2373 (reference implementation)
- **enterSentenceDrill()**: Lines 4423-4454 (similar function for sentence drill)
- **exitSentenceDrill()**: Lines 4497-4513 (similar exit function)
- **toggleDrillMic()**: Lines 4578-4591 (similar mic toggle)
- **renderDrillFeedback()**: Lines 4709-4777 (for sentence drill feedback)
