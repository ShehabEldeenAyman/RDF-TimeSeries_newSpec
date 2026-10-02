import { useState } from 'react'
import heroImg from './assets/hero.png'
import reactLogo from './assets/react.svg'
import viteLogo from './assets/vite.svg'
import './App.css'
import {TimeseriesList,timeSeriesData,TimeseriesListItem} from './Timeseries'

function App() {
    const [selectedId, setSelectedId] = useState(null)

  return (
 <>
      <h1>Time Series</h1>
      <TimeseriesList timeSeriesData={timeSeriesData} onSelect={setSelectedId} selectedId={selectedId} />

      <h1>Item Details</h1>
      {selectedId? <p><TimeseriesListItem itemId = {selectedId}/></p>:<p>not available yet</p>}

    </>
  )
}

export default App
