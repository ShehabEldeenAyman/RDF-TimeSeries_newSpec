import { useState,useEffect  } from 'react'
import './App.css'
import {TimeseriesList,TimeseriesListItem} from './Timeseries'

function App() {
    const [selectedId, setSelectedId] = useState(null)
    const [message, setMessage] = useState(null)
    const [error, setError] = useState(null)

    useEffect(()=>{
    fetch('http://localhost:8000/series').then((response)=>{
      return response.json()
    })
    .then((data)=>setMessage(data))
    .catch((err) => setError(err.message))
  },[])


    if (error) return <p>Error: {error}</p>
    if (message === null) return <p>Loading…</p>

    const selectedSeries = message.find((s) => s.id === selectedId)

  return (
 <>
      <h1>Time Series</h1>
           <TimeseriesList timeSeriesData={message} onSelect={setSelectedId} selectedId={selectedId}/>
      {/* <TimeseriesList timeSeriesData={timeSeriesData} onSelect={setSelectedId} selectedId={selectedId} /> */}

      <h1>Item Details</h1>
      {selectedSeries ? <TimeseriesListItem item={selectedSeries} /> : <p>not available yet</p>}


 

           {/* <ul>
    {message.map((s) => (
      <li key={s.id} > {s.label} ({s.tsId})</li>
    ))}
  </ul> */}

      {/* {<FetchTimeseriesList message={message} setMessage={setMessage} error={error} setError={setError} />} */}
    </>
  )

}

export default App
