// Page count for an uploaded PDF, read in the browser before the upload
// finishes. Only used to estimate how long OMR will take -- recognition is
// ~5 minutes per page, so a 12-page sheet is an hour, and a spinner with no
// number attached is not an acceptable thing to show for that long.
//
// pdfjs is imported dynamically and every failure is swallowed: a missing
// page count costs a rougher progress message, and is never worth failing
// an upload over.
export async function countPdfPages(file: File): Promise<number | null> {
  try {
    const pdfjs = await import('pdfjs-dist')
    const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url')
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default

    const doc = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise
    const pages = doc.numPages
    void doc.cleanup()
    return pages
  } catch {
    return null
  }
}
