import { useState } from 'react'
import heroImg from './assets/hero.png'
import reactLogo from './assets/react.svg'
import viteLogo from './assets/vite.svg'
import './App.css'
import {TimeseriesList,timeSeriesData} from './Timeseries'
function App() {
  return (
 <>
      <h1>Time Series</h1>
      <TimeseriesList timeSeriesData={timeSeriesData} />
    </>
  )
}

export default App
