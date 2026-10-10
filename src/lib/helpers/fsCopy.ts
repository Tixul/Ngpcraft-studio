/* eslint-disable no-await-in-loop */
import fs from "fs-extra";
import Path from "path";

interface CopyOptions {
  overwrite?: boolean;
  errorOnExist?: boolean;
  mode?: number | undefined;
  ignore?: (path: string) => boolean;
}

const copyFile = async (
  src: string,
  dest: string,
  options: CopyOptions = {},
) => {
  const { overwrite = true, errorOnExist = false, mode, ignore } = options;
  if (ignore?.(src)) {
    return;
  }
  let throwAlreadyExists = false;
  if (!overwrite) {
    try {
      await fs.lstat(dest);
      if (errorOnExist) {
        throwAlreadyExists = true;
      } else {
        return;
      }
    } catch (e) {
      // Didn't exist so copy it
    }
    if (throwAlreadyExists) {
      throw new Error(`File already exists ${dest}`);
    }
  }
  await new Promise<void>((resolve, reject) => {
    const inputStream = fs.createReadStream(src);
    const outputStream = fs.createWriteStream(dest, { mode });
    inputStream.once("error", (err) => {
      outputStream.close();
      reject(new Error(`Could not write file ${dest}: ${err}`));
    });
    // NgpCraft: resolve once the copy is written, not when reading ends (the
    // "end" of the input came before the output was flushed: createProject
    // could then read a truncated project.gbsproj under load).
    outputStream.once("error", (err) => {
      inputStream.destroy();
      reject(new Error(`Could not write file ${dest}: ${err}`));
    });
    outputStream.once("finish", () => {
      resolve();
    });
    inputStream.pipe(outputStream);
  });
};

const copyDir = async (
  src: string,
  dest: string,
  options: CopyOptions = {},
) => {
  const { ignore } = options;
  if (ignore?.(src)) {
    return;
  }
  const filePaths = await fs.readdir(src);
  await fs.ensureDir(dest);
  for (const fileName of filePaths) {
    const fileStat = await fs.lstat(`${src}/${fileName}`);
    if (fileStat.isDirectory()) {
      await copyDir(`${src}/${fileName}`, `${dest}/${fileName}`, options);
    } else {
      await copyFile(`${src}/${fileName}`, `${dest}/${fileName}`, options);
    }
  }
};

const copy = async (src: string, dest: string, options: CopyOptions = {}) => {
  // Ensure parent folder exists first
  const parentDir = Path.dirname(dest);
  await fs.ensureDir(parentDir);

  const fileStat = await fs.lstat(src);
  if (fileStat.isDirectory()) {
    await copyDir(src, dest, options);
  } else {
    await copyFile(src, dest, options);
  }
};

export default copy;
