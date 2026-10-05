import { useState,useEffect  } from 'react'


export const timeSeriesData = [
  {
    id: 'https://example.org/ex#snippet-grootdok-ec20',
    label: 'Electrical conductivity at 20 °C, Kanaal Gent-Terneuzen Grootdok (BE)',
    stationName: 'Kanaal Gent-Terneuzen Grootdok (BE)',
    tsId: '289423042',
    unit: 'µS/cm',
    from: '2021-03-03T23:45:00Z',
    until: '2025-12-18T23:15:00Z',
  },
  {
    id: 'https://example.org/ex#snippet-rodenhuizedok-ec20',
    label: 'Electrical conductivity at 20 °C, Kanaal Gent-Terneuzen Rodenhuizedok (BE)',
    stationName: 'Kanaal Gent-Terneuzen Rodenhuizedok (BE)',
    tsId: '289429042',
    unit: 'µS/cm',
    from: '2021-03-03T23:15:00Z',
    until: '2026-01-01T00:00:00Z',
  },
  {
    id: 'https://example.org/ex#snippet-autrichehaven-ec20',
    label: 'Electrical conductivity at 20 °C, Kanaal Gent-Terneuzen Autrichehaven (NL)',
    stationName: 'Kanaal Gent-Terneuzen Autrichehaven (NL)',
    tsId: '289435042',
    unit: 'µS/cm',
    from: '2021-03-03T23:15:00Z',
    until: '2025-10-07T07:45:00Z',
  },
  {
    id: 'https://example.org/ex#snippet-zevenaarshaven-ec20',
    label: 'Electrical conductivity at 20 °C, Kanaal Gent-Terneuzen Zevenaarshaven (NL)',
    stationName: 'Kanaal Gent-Terneuzen Zevenaarshaven (NL)',
    tsId: '289441042',
    unit: 'µS/cm',
    from: '2021-03-03T23:15:00Z',
    until: '2026-01-01T00:00:00Z',
  },
]

export function TimeseriesListItem({itemId}){
    const item = timeSeriesData.find(item => item.id === itemId);
    return(
        <div>
          <h2>{item.stationName}</h2>,
          <h2>{item.tsId}</h2>,
          <h2>{item.unit}</h2>,
          <h2>{item.from}</h2>,
          <h2>{item.until}</h2>,
        </div>
    )
}

export function TimeseriesList({timeSeriesData, onSelect,selectedId}){
      return (
    <ul>
      {timeSeriesData.map((item) => (
        <li key={item.id} onClick={()=>onSelect(item.id)} style={{cursor: 'pointer', fontWeight: item.id === selectedId? 'bold':'normal'}}>
          {item.label}
          {/* <br />
          {item.stationName},
          {item.tsId},
          {item.unit},
          {item.from},
          {item.until}, */}
        </li>
      ))}
    </ul>
  )
}

export function FetchTimeseriesList({message, setMessage,error, setError}){
  useEffect(()=>{
    fetch('http://localhost:8000/series').then((response)=>{
      return response.json()
    })
    .then((data)=>setMessage(data))
    .catch((err) => setError(err.error))
  },[])

    if (message === null) return <p>Loading…</p>


  return(
     <ul>
    {message.map((s) => (
      <li key={s.id}>{s.label} ({s.tsId})</li>
    ))}
  </ul>
  );

}