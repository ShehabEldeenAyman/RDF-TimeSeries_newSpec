import { useState,useEffect  } from 'react'
import heroImg from './assets/hero.png'
import reactLogo from './assets/react.svg'
import viteLogo from './assets/vite.svg'
import './App.css'
import {TimeseriesList,timeSeriesData,TimeseriesListItem,FetchTimeseriesList} from './Timeseries'

function App() {
    const [selectedId, setSelectedId] = useState(null)
    const [message, setMessage] = useState(null)
    const [error, setError] = useState(null)

  return (
 <>
      <h1>Time Series</h1>
      {/* <TimeseriesList timeSeriesData={timeSeriesData} onSelect={setSelectedId} selectedId={selectedId} /> */}

      {/* <h1>Item Details</h1>
      {selectedId? <p><TimeseriesListItem itemId = {selectedId}/></p>:<p>not available yet</p>} */}
      
      {<FetchTimeseriesList message={message} setMessage={setMessage} error={error} setError={setError} />}
    </>
  )
}

export default App
